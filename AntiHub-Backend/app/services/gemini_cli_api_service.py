"""
GeminiCLI 推理调用服务

目标：
- 使用已落库的 GeminiCLI OAuth 凭证调用 cloudcode-pa（generateContent / streamGenerateContent）
- 对外提供两种兼容输出：
  1) OpenAI Chat Completions（/v1/chat/completions）
  2) Gemini v1beta（/v1beta/models/{model}:generateContent / :streamGenerateContent）
- 支持模型内置网络搜索：tools[].google_search / tools[].googleSearch
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RedisClient
from app.repositories.gemini_cli_account_repository import GeminiCLIAccountRepository
from app.services.gemini_cli_service import (
    CLOUDCODE_PA_BASE_URL,
    DEFAULT_CLIENT_METADATA,
    DEFAULT_USER_AGENT,
    DEFAULT_X_GOOG_API_CLIENT,
    GeminiCLIService,
)

logger = logging.getLogger(__name__)

MODELS_CACHE_TTL_SECONDS = 24 * 60 * 60
MODELS_FALLBACK_CACHE_TTL_SECONDS = 5 * 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _pick_first_project_id(project_id: Optional[str]) -> str:
    raw = (project_id or "").strip()
    if not raw:
        return ""
    for part in raw.split(","):
        v = part.strip()
        if v:
            return v
    return ""


def _default_safety_settings() -> List[Dict[str, str]]:
    return [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
    ]


def _ensure_default_safety_settings(request_obj: Dict[str, Any]) -> None:
    if isinstance(request_obj, dict) and "safetySettings" not in request_obj:
        request_obj["safetySettings"] = _default_safety_settings()


def _parse_rfc3339_to_unix(value: Optional[str]) -> Optional[int]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _extract_usage_from_gemini_response(response_obj: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """
    从 Gemini（含 GeminiCLI 包装的 response 字段）里提取 token 用量。

    返回：(prompt_tokens, completion_tokens, total_tokens, reasoning_tokens)
    """
    usage = response_obj.get("usageMetadata")
    if not isinstance(usage, dict):
        return 0, 0, 0, 0

    prompt = int(usage.get("promptTokenCount") or 0)
    completion = int(usage.get("candidatesTokenCount") or 0)
    total = int(usage.get("totalTokenCount") or (prompt + completion))
    thoughts = int(usage.get("thoughtsTokenCount") or 0)

    return prompt + thoughts, completion, total, thoughts


def _openai_error_sse(message: str, *, code: int = 500, error_type: str = "upstream_error") -> bytes:
    payload = {
        "error": {
            "message": (message or "upstream_error"),
            "type": error_type,
            "code": int(code or 500),
        }
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _openai_done_sse() -> bytes:
    return b"data: [DONE]\n\n"


@dataclass
class _OpenAIStreamState:
    created: int = 0
    function_index: int = 0


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    try:
        return json.loads(s)
    except Exception:
        return value


def _data_url_to_inline_data(url: str) -> Optional[Dict[str, Any]]:
    """
    OpenAI image_url 常见的 data URL：
    data:<mime>;base64,<payload>
    """
    raw = (url or "").strip()
    if not raw.startswith("data:"):
        return None
    without_prefix = raw[5:]
    if ";base64," not in without_prefix:
        return None
    mime, b64 = without_prefix.split(";base64,", 1)
    mime = (mime or "").strip() or "image/png"
    b64 = (b64 or "").strip()
    if not b64:
        return None
    # 注意：cloudcode-pa 的历史请求里使用 mime_type（snake_case）
    return {"inlineData": {"mime_type": mime, "data": b64}}


def _normalize_openai_tools_to_gemini_tools(tools: Any) -> Optional[List[Dict[str, Any]]]:
    """
    OpenAI Chat tools -> Gemini(GeminiCLI) request.tools

    兼容两种写法：
    - Gemini 风格：{"google_search": {...}} / {"googleSearch": {...}}
    - OpenAI built-in：{"type":"web_search", ...}（尽力映射为 googleSearch）
    - OpenAI function tools：{"type":"function","function":{...}}
    """
    if not isinstance(tools, list) or not tools:
        return None

    function_decls: List[Dict[str, Any]] = []
    google_search_nodes: List[Dict[str, Any]] = []

    for t in tools:
        if not isinstance(t, dict):
            continue

        t_type = (t.get("type") or "").strip()

        if t_type == "function":
            fn = t.get("function")
            if not isinstance(fn, dict):
                continue

            decl = dict(fn)
            if "parametersJsonSchema" not in decl:
                if "parameters" in decl and isinstance(decl.get("parameters"), dict):
                    decl["parametersJsonSchema"] = decl.pop("parameters")
                else:
                    decl["parametersJsonSchema"] = {"type": "object", "properties": {}}
            decl.pop("strict", None)
            function_decls.append(decl)
            continue

        if t_type == "web_search":
            cfg = {k: v for k, v in t.items() if k != "type"}
            google_search_nodes.append({"googleSearch": cfg or {}})
            continue

        if t_type == "google_search":
            cfg = {k: v for k, v in t.items() if k != "type"}
            google_search_nodes.append({"googleSearch": cfg or {}})
            continue

        if "google_search" in t:
            google_search_nodes.append({"googleSearch": t.get("google_search")})
            continue
        if "googleSearch" in t:
            google_search_nodes.append({"googleSearch": t.get("googleSearch")})
            continue

    out: List[Dict[str, Any]] = []
    if function_decls:
        out.append({"functionDeclarations": function_decls})
    out.extend(google_search_nodes)
    return out or None


def _openai_messages_to_gemini_contents(messages: Any) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    OpenAI messages -> (systemInstruction, contents)
    """
    if not isinstance(messages, list) or not messages:
        return None, []

    tool_call_id_to_name: Dict[str, str] = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        if (m.get("role") or "").strip() != "assistant":
            continue
        tcs = m.get("tool_calls")
        if not isinstance(tcs, list):
            continue
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            if (tc.get("type") or "").strip() != "function":
                continue
            tc_id = (tc.get("id") or "").strip()
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = (fn.get("name") or "").strip() if isinstance(fn, dict) else ""
            if tc_id and name:
                tool_call_id_to_name[tc_id] = name

    tool_responses: Dict[str, Any] = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        if (m.get("role") or "").strip() != "tool":
            continue
        tool_call_id = (m.get("tool_call_id") or "").strip()
        if tool_call_id:
            tool_responses[tool_call_id] = m.get("content")

    system_parts: List[Dict[str, Any]] = []
    contents: List[Dict[str, Any]] = []

    for m in messages:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").strip()
        content = m.get("content")

        if role in ("system", "developer") and len(messages) > 1:
            texts: List[str] = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, dict) and (content.get("type") or "").strip() == "text":
                texts = [str(content.get("text") or "")]
            elif isinstance(content, list):
                for it in content:
                    if isinstance(it, dict) and (it.get("type") or "").strip() == "text":
                        texts.append(str(it.get("text") or ""))
            for t in texts:
                t = (t or "").strip()
                if t:
                    system_parts.append({"text": t})
            continue

        if role == "user" or (role in ("system", "developer") and len(messages) == 1):
            node: Dict[str, Any] = {"role": "user", "parts": []}
            if isinstance(content, str):
                if content.strip():
                    node["parts"].append({"text": content})
            elif isinstance(content, list):
                for it in content:
                    if not isinstance(it, dict):
                        continue
                    t = (it.get("type") or "").strip()
                    if t == "text":
                        node["parts"].append({"text": it.get("text")})
                    elif t == "image_url":
                        image_url = (
                            (it.get("image_url") or {}).get("url")
                            if isinstance(it.get("image_url"), dict)
                            else it.get("image_url")
                        )
                        inline = _data_url_to_inline_data(str(image_url or ""))
                        if inline:
                            inline["thoughtSignature"] = "skip_thought_signature_validator"
                            node["parts"].append(inline)
            if node["parts"]:
                contents.append(node)
            continue

        if role == "assistant":
            node: Dict[str, Any] = {"role": "model", "parts": []}

            if isinstance(content, str):
                if content.strip():
                    node["parts"].append({"text": content})
            elif isinstance(content, list):
                for it in content:
                    if not isinstance(it, dict):
                        continue
                    t = (it.get("type") or "").strip()
                    if t == "text":
                        node["parts"].append({"text": it.get("text")})
                    elif t == "image_url":
                        image_url = (
                            (it.get("image_url") or {}).get("url")
                            if isinstance(it.get("image_url"), dict)
                            else it.get("image_url")
                        )
                        inline = _data_url_to_inline_data(str(image_url or ""))
                        if inline:
                            inline["thoughtSignature"] = "skip_thought_signature_validator"
                            node["parts"].append(inline)

            tcs = m.get("tool_calls")
            tool_call_ids: List[str] = []
            if isinstance(tcs, list):
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    if (tc.get("type") or "").strip() != "function":
                        continue
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    fname = (fn.get("name") or "").strip() if isinstance(fn, dict) else ""
                    fargs_raw = fn.get("arguments") if isinstance(fn, dict) else None
                    fargs = _safe_json_loads(fargs_raw) if isinstance(fargs_raw, str) else fargs_raw
                    node["parts"].append(
                        {
                            "functionCall": {"name": fname, "args": fargs if isinstance(fargs, dict) else {}},
                            "thoughtSignature": "skip_thought_signature_validator",
                        }
                    )
                    tc_id = (tc.get("id") or "").strip()
                    if tc_id:
                        tool_call_ids.append(tc_id)

            if node["parts"]:
                contents.append(node)

            if tool_call_ids:
                tool_node: Dict[str, Any] = {"role": "user", "parts": []}
                for tc_id in tool_call_ids:
                    name = tool_call_id_to_name.get(tc_id) or ""
                    if not name:
                        continue
                    raw_resp = tool_responses.get(tc_id)
                    resp_val = _safe_json_loads(raw_resp) if isinstance(raw_resp, str) else raw_resp
                    tool_node["parts"].append(
                        {"functionResponse": {"name": name, "response": {"result": resp_val}}}
                    )
                if tool_node["parts"]:
                    contents.append(tool_node)

    system_instruction = {"role": "user", "parts": system_parts} if system_parts else None
    return system_instruction, contents


def _openai_request_to_gemini_cli_payload(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    把 OpenAI Chat Completions request 转成 GeminiCLI 的 payload（不含 project）。
    """
    model = (request_data.get("model") or "").strip() or "gemini-2.5-pro"
    out: Dict[str, Any] = {"project": "", "request": {"contents": []}, "model": model}

    req_obj = out["request"]

    gen_cfg: Dict[str, Any] = {}
    if isinstance(request_data.get("temperature"), (int, float)):
        gen_cfg["temperature"] = request_data["temperature"]
    if isinstance(request_data.get("top_p"), (int, float)):
        gen_cfg["topP"] = request_data["top_p"]
    if isinstance(request_data.get("top_k"), (int, float)):
        gen_cfg["topK"] = request_data["top_k"]
    if isinstance(request_data.get("n"), int) and int(request_data["n"]) > 1:
        gen_cfg["candidateCount"] = int(request_data["n"])
    if isinstance(request_data.get("max_tokens"), int) and int(request_data["max_tokens"]) > 0:
        gen_cfg["maxOutputTokens"] = int(request_data["max_tokens"])

    stop = request_data.get("stop")
    if isinstance(stop, str) and stop.strip():
        gen_cfg["stopSequences"] = [stop.strip()]
    elif isinstance(stop, list):
        seqs = [str(s).strip() for s in stop if str(s).strip()]
        if seqs:
            gen_cfg["stopSequences"] = seqs

    if gen_cfg:
        req_obj["generationConfig"] = gen_cfg

    system_instruction, contents = _openai_messages_to_gemini_contents(request_data.get("messages"))
    if system_instruction:
        req_obj["systemInstruction"] = system_instruction
    req_obj["contents"] = contents

    tools_node = _normalize_openai_tools_to_gemini_tools(request_data.get("tools"))
    if tools_node:
        req_obj["tools"] = tools_node

    _ensure_default_safety_settings(req_obj)
    return out


def _normalize_fn_decl(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    out = dict(item)
    if "parametersJsonSchema" not in out:
        if "parameters" in out and isinstance(out.get("parameters"), dict):
            out["parametersJsonSchema"] = out.pop("parameters")
        else:
            out["parametersJsonSchema"] = {"type": "object", "properties": {}}
    out.pop("strict", None)
    return out


def _normalize_gemini_request_to_cli_request(model: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gemini v1beta request -> GeminiCLI request（不含 project）。
    """
    req_obj = dict(request_data or {})

    # model 由 path 提供；如果 body 里也有，忽略它
    req_obj.pop("model", None)

    # 兼容 system_instruction（snake）-> systemInstruction（camel）
    if "systemInstruction" not in req_obj and "system_instruction" in req_obj:
        req_obj["systemInstruction"] = req_obj.pop("system_instruction")

    tools_in = req_obj.get("tools")
    if isinstance(tools_in, list):
        tools_out: List[Dict[str, Any]] = []
        for t in tools_in:
            if not isinstance(t, dict):
                continue

            if "functionDeclarations" in t:
                fd = t.get("functionDeclarations")
                if isinstance(fd, list):
                    tools_out.append({"functionDeclarations": [_normalize_fn_decl(x) for x in fd]})
                continue

            if "function_declarations" in t:
                fd = t.get("function_declarations")
                if isinstance(fd, list):
                    tools_out.append({"functionDeclarations": [_normalize_fn_decl(x) for x in fd]})
                continue

            if "googleSearch" in t:
                tools_out.append({"googleSearch": t.get("googleSearch")})
                continue

            if "google_search" in t:
                tools_out.append({"googleSearch": t.get("google_search")})
                continue

            tools_out.append(t)

        req_obj["tools"] = tools_out

    _ensure_default_safety_settings(req_obj)
    return {"project": "", "request": req_obj, "model": model}


_tool_call_counter = 0


def _next_tool_call_id(name: str) -> str:
    global _tool_call_counter
    _tool_call_counter += 1
    n = (name or "tool").strip() or "tool"
    return f"{n}-{int(time.time() * 1_000_000)}-{_tool_call_counter}-{uuid4().hex[:8]}"


def _gemini_cli_event_to_openai_chunks(
    raw_event: Dict[str, Any],
    *,
    state: _OpenAIStreamState,
) -> List[Dict[str, Any]]:
    """
    单个 GeminiCLI SSE event（JSON）-> 0..N 个 OpenAI chat.completion.chunk payload。
    """
    response = raw_event.get("response")
    if not isinstance(response, dict):
        return []

    model_version = (response.get("modelVersion") or "").strip()
    response_id = (response.get("responseId") or "").strip()

    created = _parse_rfc3339_to_unix(response.get("createTime"))
    if created is not None:
        state.created = created
    created_ts = state.created or int(time.time())

    finish_reason = None
    candidates = response.get("candidates")
    if isinstance(candidates, list) and candidates:
        fr = candidates[0].get("finishReason") if isinstance(candidates[0], dict) else None
        if isinstance(fr, str) and fr.strip():
            finish_reason = fr.strip().lower()

    prompt_tok, completion_tok, total_tok, reasoning_tok = _extract_usage_from_gemini_response(response)

    parts: List[Any] = []
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        if isinstance(content, dict) and isinstance(content.get("parts"), list):
            parts = content["parts"]

    chunks: List[Dict[str, Any]] = []

    if not parts:
        payload: Dict[str, Any] = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_version,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": None, "content": None, "reasoning_content": None, "tool_calls": None},
                    "finish_reason": finish_reason,
                    "native_finish_reason": finish_reason,
                }
            ],
        }
        if total_tok:
            payload["usage"] = {
                "prompt_tokens": prompt_tok,
                "completion_tokens": completion_tok,
                "total_tokens": total_tok,
            }
            if reasoning_tok:
                payload["usage"]["completion_tokens_details"] = {"reasoning_tokens": reasoning_tok}
        return [payload]

    for part in parts:
        if not isinstance(part, dict):
            continue

        thought_signature = part.get("thoughtSignature") or part.get("thought_signature")
        has_thought_signature = isinstance(thought_signature, str) and thought_signature.strip() != ""

        text_val = part.get("text")
        function_call = part.get("functionCall") or part.get("function_call")
        inline_data = part.get("inlineData") or part.get("inline_data")
        has_payload = text_val is not None or function_call is not None or inline_data is not None
        if has_thought_signature and not has_payload:
            continue

        payload = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_version,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": None, "content": None, "reasoning_content": None, "tool_calls": None},
                    "finish_reason": finish_reason,
                    "native_finish_reason": finish_reason,
                }
            ],
        }
        if total_tok:
            payload["usage"] = {
                "prompt_tokens": prompt_tok,
                "completion_tokens": completion_tok,
                "total_tokens": total_tok,
            }
            if reasoning_tok:
                payload["usage"]["completion_tokens_details"] = {"reasoning_tokens": reasoning_tok}

        if isinstance(text_val, str) and text_val != "":
            payload["choices"][0]["delta"]["role"] = "assistant"
            if bool(part.get("thought")):
                payload["choices"][0]["delta"]["reasoning_content"] = text_val
            else:
                payload["choices"][0]["delta"]["content"] = text_val
            chunks.append(payload)
            continue

        if isinstance(function_call, dict) and (function_call.get("name") or "").strip():
            fname = (function_call.get("name") or "").strip()
            fargs = function_call.get("args")
            if isinstance(fargs, (dict, list)):
                fargs_str = json.dumps(fargs, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(fargs, str):
                fargs_str = fargs
            else:
                fargs_str = "{}"

            payload["choices"][0]["delta"]["role"] = "assistant"
            payload["choices"][0]["delta"]["tool_calls"] = [
                {
                    "id": _next_tool_call_id(fname),
                    "index": state.function_index,
                    "type": "function",
                    "function": {"name": fname, "arguments": fargs_str},
                }
            ]
            state.function_index += 1
            payload["choices"][0]["finish_reason"] = "tool_calls"
            payload["choices"][0]["native_finish_reason"] = "tool_calls"
            chunks.append(payload)
            continue

        if isinstance(inline_data, dict) and (inline_data.get("data") or "").strip():
            mime_type = (
                (inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png").strip()
            )
            b64 = (inline_data.get("data") or "").strip()
            payload["choices"][0]["delta"]["role"] = "assistant"
            payload["choices"][0]["delta"]["images"] = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}
            ]
            chunks.append(payload)
            continue

    return chunks


def _gemini_cli_response_to_openai_response(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    GeminiCLI 非流式响应（包含 response 字段）-> OpenAI Chat Completions JSON
    """
    response = raw_json.get("response")
    if not isinstance(response, dict):
        response = raw_json if isinstance(raw_json, dict) else {}

    response_id = (response.get("responseId") or "").strip() or f"gemini-cli-{uuid4().hex}"
    model_version = (response.get("modelVersion") or "").strip()
    created = _parse_rfc3339_to_unix(response.get("createTime")) or int(time.time())

    candidates = response.get("candidates") if isinstance(response.get("candidates"), list) else []
    first = candidates[0] if candidates else {}
    finish_reason_raw = (first.get("finishReason") if isinstance(first, dict) else None) or ""
    finish_reason = str(finish_reason_raw).strip().lower() if str(finish_reason_raw).strip() else "stop"

    parts: List[Any] = []
    content_obj = first.get("content") if isinstance(first, dict) else None
    if isinstance(content_obj, dict) and isinstance(content_obj.get("parts"), list):
        parts = content_obj["parts"]

    content_texts: List[str] = []
    reasoning_texts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    images: List[Dict[str, Any]] = []

    tool_index = 0
    for part in parts:
        if not isinstance(part, dict):
            continue

        thought_signature = part.get("thoughtSignature") or part.get("thought_signature")
        has_thought_signature = isinstance(thought_signature, str) and thought_signature.strip() != ""

        text_val = part.get("text")
        function_call = part.get("functionCall") or part.get("function_call")
        inline_data = part.get("inlineData") or part.get("inline_data")
        has_payload = text_val is not None or function_call is not None or inline_data is not None
        if has_thought_signature and not has_payload:
            continue

        if isinstance(text_val, str) and text_val != "":
            if bool(part.get("thought")):
                reasoning_texts.append(text_val)
            else:
                content_texts.append(text_val)
            continue

        if isinstance(function_call, dict) and (function_call.get("name") or "").strip():
            fname = (function_call.get("name") or "").strip()
            fargs = function_call.get("args")
            if isinstance(fargs, (dict, list)):
                fargs_str = json.dumps(fargs, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(fargs, str):
                fargs_str = fargs
            else:
                fargs_str = "{}"
            tool_calls.append(
                {
                    "id": _next_tool_call_id(fname),
                    "index": tool_index,
                    "type": "function",
                    "function": {"name": fname, "arguments": fargs_str},
                }
            )
            tool_index += 1
            continue

        if isinstance(inline_data, dict) and (inline_data.get("data") or "").strip():
            mime_type = (
                (inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png").strip()
            )
            b64 = (inline_data.get("data") or "").strip()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}})

    prompt_tok, completion_tok, total_tok, reasoning_tok = _extract_usage_from_gemini_response(response)

    message: Dict[str, Any] = {"role": "assistant", "content": "".join(content_texts) if content_texts else ""}
    if reasoning_texts:
        message["reasoning_content"] = "".join(reasoning_texts)
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    if images:
        message["images"] = images

    out: Dict[str, Any] = {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model_version,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "native_finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tok,
            "completion_tokens": completion_tok,
            "total_tokens": total_tok,
        },
    }
    if reasoning_tok:
        out["usage"]["completion_tokens_details"] = {"reasoning_tokens": reasoning_tok}
    return out


class GeminiCLIAPIService:
    SUPPORTED_MODELS = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]

    def __init__(self, db: AsyncSession, redis: RedisClient):
        self.db = db
        self.redis = redis
        self.repo = GeminiCLIAccountRepository(db)
        self.account_service = GeminiCLIService(db, redis)

    async def openai_list_models(self, *, user_id: int) -> Dict[str, Any]:
        models = await self._get_models_best_effort(user_id=user_id)
        return {"object": "list", "data": [{"id": m, "object": "model"} for m in models]}

    def _models_cache_key(self, user_id: int) -> str:
        return f"gemini_cli_models:{user_id}"

    async def _get_models_best_effort(self, *, user_id: int) -> List[str]:
        cache_key = self._models_cache_key(user_id)
        try:
            cached = await self.redis.get_json(cache_key)
            if isinstance(cached, list) and cached:
                models = [str(x).strip() for x in cached if str(x).strip()]
                if models:
                    return models
        except Exception:
            pass

        models_from_quota = await self._fetch_models_from_quota_best_effort(user_id=user_id)
        models = models_from_quota or list(self.SUPPORTED_MODELS)
        ttl = MODELS_CACHE_TTL_SECONDS if models_from_quota else MODELS_FALLBACK_CACHE_TTL_SECONDS

        try:
            await self.redis.set_json(cache_key, models, expire=ttl)
        except Exception:
            pass

        return models

    async def _fetch_models_from_quota_best_effort(self, *, user_id: int) -> List[str]:
        """
        尝试从 retrieveUserQuota 的 buckets 里拿 model_id（更贴近账号真实可用模型）。

        失败/为空就返回 []，调用方兜底用写死列表。
        """
        try:
            accounts = await self.repo.list_enabled_by_user_id(user_id)
            if not accounts:
                return []

            account = accounts[0]
            quota = await self.account_service.get_account_quota(user_id, int(account.id))
            data = quota.get("data") if isinstance(quota, dict) else None
            if not isinstance(data, dict):
                return []

            buckets = data.get("buckets")
            if not isinstance(buckets, list):
                return []

            out: List[str] = []
            seen = set()
            for b in buckets:
                if not isinstance(b, dict):
                    continue
                mid = b.get("model_id") or b.get("modelId")
                if not isinstance(mid, str):
                    continue
                mid = mid.strip()
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                out.append(mid)

            return out
        except Exception:
            return []

    async def _prepare_account(self, user_id: int) -> Tuple[str, str]:
        """
        选择一个可用账号，返回 (access_token, project_id)
        """
        accounts = await self.repo.list_enabled_by_user_id(user_id)
        if not accounts:
            raise ValueError("未找到可用的 GeminiCLI 账号（请先在面板完成 OAuth 并启用账号）")

        account = accounts[0]
        project_id = _pick_first_project_id(getattr(account, "project_id", None))
        if not project_id:
            raise ValueError("GeminiCLI 账号缺少 project_id（请先在账号详情里填写 GCP Project ID）")

        access_token = await self.account_service.get_valid_access_token(user_id, int(account.id))

        # best-effort 记录 last_used_at；commit 由 get_db() 依赖统一处理
        try:
            await self.repo.update_last_used_at(int(account.id), user_id)
        except Exception:
            pass

        return access_token, project_id

    def _headers(self, access_token: str, *, accept: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": accept,
            "User-Agent": DEFAULT_USER_AGENT,
            "X-Goog-Api-Client": DEFAULT_X_GOOG_API_CLIENT,
            "Client-Metadata": DEFAULT_CLIENT_METADATA,
        }

    async def openai_chat_completions(self, *, user_id: int, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        OpenAI Chat（非流式）：调用 cloudcode-pa generateContent，并返回 OpenAI JSON。
        """
        access_token, project_id = await self._prepare_account(user_id)
        payload = _openai_request_to_gemini_cli_payload(request_data)
        payload["project"] = project_id

        url = f"{CLOUDCODE_PA_BASE_URL}:generateContent"
        headers = self._headers(access_token, accept="application/json")

        async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0, connect=60.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                try:
                    error_data = resp.json()
                except Exception:
                    error_data = {"detail": resp.text}
                err = httpx.HTTPStatusError(
                    message=f"GeminiCLI upstream error: {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
                err.response_data = error_data
                raise err

            raw = resp.json()
            if not isinstance(raw, dict):
                raise ValueError("GeminiCLI 上游响应格式异常（非对象）")
            return _gemini_cli_response_to_openai_response(raw)

    async def openai_chat_completions_stream(
        self,
        *,
        user_id: int,
        request_data: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """
        OpenAI Chat（流式）：调用 cloudcode-pa streamGenerateContent?alt=sse，
        并把每个 event 翻译成 OpenAI SSE（data: {...}\\n\\n + [DONE]）。
        """
        access_token, project_id = await self._prepare_account(user_id)
        payload = _openai_request_to_gemini_cli_payload(request_data)
        payload["project"] = project_id

        url = f"{CLOUDCODE_PA_BASE_URL}:streamGenerateContent?alt=sse"
        headers = self._headers(access_token, accept="text/event-stream")

        state = _OpenAIStreamState(created=int(time.time()), function_index=0)

        async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0, connect=60.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    msg = body.decode("utf-8", errors="replace")[:500]
                    yield _openai_error_sse(msg or "upstream_error", code=resp.status_code)
                    yield _openai_done_sse()
                    return

                buffer = b""
                async for chunk in resp.aiter_raw():
                    if not chunk:
                        continue
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip(b"\r")
                        if not line or not line.startswith(b"data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        try:
                            event_obj = json.loads(data.decode("utf-8", errors="replace"))
                        except Exception:
                            continue
                        if not isinstance(event_obj, dict):
                            continue

                        for payload_obj in _gemini_cli_event_to_openai_chunks(event_obj, state=state):
                            yield f"data: {json.dumps(payload_obj, ensure_ascii=False)}\n\n".encode("utf-8")

                yield _openai_done_sse()

    async def gemini_generate_content(
        self,
        *,
        user_id: int,
        model: str,
        request_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Gemini v1beta generateContent（非流式）：返回 Gemini 标准 JSON。
        """
        access_token, project_id = await self._prepare_account(user_id)
        payload = _normalize_gemini_request_to_cli_request(model, request_data)
        payload["project"] = project_id

        url = f"{CLOUDCODE_PA_BASE_URL}:generateContent"
        headers = self._headers(access_token, accept="application/json")

        async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0, connect=60.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                try:
                    error_data = resp.json()
                except Exception:
                    error_data = {"detail": resp.text}
                err = httpx.HTTPStatusError(
                    message=f"GeminiCLI upstream error: {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
                err.response_data = error_data
                raise err

            raw = resp.json()
            if not isinstance(raw, dict):
                raise ValueError("GeminiCLI 上游响应格式异常（非对象）")
            response_obj = raw.get("response")
            if isinstance(response_obj, dict):
                return response_obj
            return raw

    async def gemini_stream_generate_content(
        self,
        *,
        user_id: int,
        model: str,
        request_data: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """
        Gemini v1beta streamGenerateContent：输出 `data: <GeminiResponse>\\n\\n` 的 SSE（不发送 [DONE]）。
        """
        access_token, project_id = await self._prepare_account(user_id)
        payload = _normalize_gemini_request_to_cli_request(model, request_data)
        payload["project"] = project_id

        url = f"{CLOUDCODE_PA_BASE_URL}:streamGenerateContent?alt=sse"
        headers = self._headers(access_token, accept="text/event-stream")

        async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0, connect=60.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    msg = body.decode("utf-8", errors="replace")[:500]
                    yield f"data: {json.dumps({'error': {'message': msg or 'upstream_error', 'code': resp.status_code}}, ensure_ascii=False)}\n\n".encode(
                        "utf-8"
                    )
                    return

                buffer = b""
                async for chunk in resp.aiter_raw():
                    if not chunk:
                        continue
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip(b"\r")
                        if not line or not line.startswith(b"data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        try:
                            event_obj = json.loads(data.decode("utf-8", errors="replace"))
                        except Exception:
                            continue
                        if not isinstance(event_obj, dict):
                            continue
                        resp_obj = event_obj.get("response")
                        if not isinstance(resp_obj, dict):
                            continue
                        yield f"data: {json.dumps(resp_obj, ensure_ascii=False)}\n\n".encode("utf-8")
