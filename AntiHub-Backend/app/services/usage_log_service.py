"""
用量统计服务

目标：
1) 记录所有调用（成功/失败都要记录）
2) 流式与非流式都尽量提取 usage（tokens）
3) 记录失败原因但不影响主流程
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from app.db.session import get_session_maker
from app.models.usage_log import UsageLog

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_LENGTH = 2000


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _truncate_message(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    msg = str(message)
    if len(msg) <= MAX_ERROR_MESSAGE_LENGTH:
        return msg
    return msg[:MAX_ERROR_MESSAGE_LENGTH] + "…"


def extract_openai_usage(payload: Dict[str, Any]) -> Tuple[int, int, int]:
    """
    从 OpenAI/兼容格式中提取 token 用量。

    兼容：
    - payload.usage.prompt_tokens / completion_tokens / total_tokens
    - payload.usage.input_tokens / output_tokens
    - payload.x_groq.usage.prompt_tokens / completion_tokens / total_tokens
    """
    usage: Dict[str, Any] = {}

    raw_usage = payload.get("usage")
    if isinstance(raw_usage, dict):
        usage = raw_usage

    # OpenAI Responses streaming: data 里是 event wrapper（含 response 字段）
    if not usage:
        response_obj = payload.get("response")
        if isinstance(response_obj, dict) and isinstance(response_obj.get("usage"), dict):
            usage = response_obj["usage"]

    if not usage:
        x_groq = payload.get("x_groq")
        if isinstance(x_groq, dict) and isinstance(x_groq.get("usage"), dict):
            usage = x_groq["usage"]

    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    total_tokens = usage.get("total_tokens", None)

    input_tokens_i = _safe_int(input_tokens, 0)
    output_tokens_i = _safe_int(output_tokens, 0)
    total_tokens_i = _safe_int(total_tokens, input_tokens_i + output_tokens_i)

    return input_tokens_i, output_tokens_i, total_tokens_i


@dataclass
class SSEUsageTracker:
    """
    轻量 SSE 解析器：从流式响应里尽量捕获 usage 和 error。

    只解析以 `data: ` 开头的行，忽略 event: 等字段。
    """

    buffer: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    success: bool = True
    status_code: Optional[int] = None
    error_message: Optional[str] = None
    _seen_usage: bool = False

    def feed(self, chunk: bytes) -> None:
        try:
            self.buffer += chunk.decode("utf-8", errors="replace")
        except Exception:
            return

        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if data_str == "[DONE]":
                continue

            try:
                payload = json.loads(data_str)
            except Exception:
                continue

            if isinstance(payload, dict):
                # usage
                in_tok, out_tok, total_tok = extract_openai_usage(payload)
                if in_tok or out_tok or total_tok:
                    self.input_tokens = in_tok
                    self.output_tokens = out_tok
                    self.total_tokens = total_tok
                    self._seen_usage = True

                # error（兼容 Responses: response.error）
                err = None
                if "error" in payload:
                    err = payload.get("error")
                else:
                    response_obj = payload.get("response")
                    if isinstance(response_obj, dict) and response_obj.get("error") is not None:
                        err = response_obj.get("error")

                if err is not None:
                    self.success = False
                    if isinstance(err, dict):
                        self.error_message = _truncate_message(
                            err.get("message") or err.get("detail") or str(err)
                        )
                        code = err.get("code") or err.get("status") or err.get("status_code")
                        self.status_code = _safe_int(code, self.status_code or 500)
                    else:
                        self.error_message = _truncate_message(str(err))
                        self.status_code = self.status_code or 500

    def finalize(self) -> None:
        if not self._seen_usage:
            self.total_tokens = self.input_tokens + self.output_tokens


class UsageLogService:
    @classmethod
    async def record(
        cls,
        *,
        user_id: int,
        api_key_id: Optional[int],
        endpoint: str,
        method: str,
        model_name: Optional[str],
        config_type: Optional[str],
        stream: bool,
        quota_consumed: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        success: bool = True,
        status_code: Optional[int] = None,
        error_message: Optional[str] = None,
        duration_ms: int = 0,
    ) -> None:
        """
        写 usage_log（失败也写），写入失败不影响主流程。
        """
        try:
            session_maker = get_session_maker()
            async with session_maker() as db:
                log = UsageLog(
                    user_id=user_id,
                    api_key_id=api_key_id,
                    endpoint=endpoint,
                    method=method,
                    model_name=model_name,
                    config_type=config_type,
                    stream=stream,
                    quota_consumed=quota_consumed,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    success=success,
                    status_code=status_code,
                    error_message=_truncate_message(error_message),
                    duration_ms=duration_ms,
                )
                db.add(log)
                await db.commit()
        except Exception as e:
            logger.warning(f"记录 usage_log 失败: {e}")
