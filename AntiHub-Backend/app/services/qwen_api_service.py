from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Optional, Sequence
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RedisClient
from app.models.qwen_account import QwenAccount
from app.utils.encryption import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)


QWEN_OAUTH_DEVICE_CODE_ENDPOINT = "https://chat.qwen.ai/api/v1/oauth2/device/code"
QWEN_OAUTH_TOKEN_ENDPOINT = "https://chat.qwen.ai/api/v1/oauth2/token"
QWEN_OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
QWEN_OAUTH_SCOPE = "openid profile email model.completion"
QWEN_OAUTH_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

QWEN_DEFAULT_RESOURCE_HOST = "portal.qwen.ai"

QWEN_USER_AGENT = "google-api-nodejs-client/9.15.1"
QWEN_X_GOOG_API_CLIENT = "gl-node/22.17.0"
QWEN_CLIENT_METADATA = "ideType=IDE_UNSPECIFIED,platform=PLATFORM_UNSPECIFIED,pluginType=GEMINI"

QWEN_OAUTH_STATE_KEY_PREFIX = "qwen_oauth:"
QWEN_OAUTH_STATE_TTL_MIN_SECONDS = 300
QWEN_OAUTH_STATE_TTL_MAX_SECONDS = 3600


class QwenOAuthPendingError(Exception):
    pass


class QwenOAuthSlowDownError(Exception):
    pass


class QwenAPIError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _trimmed_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _generate_code_verifier() -> str:
    # 32 bytes -> 43 chars base64url，足够用于 PKCE
    return _base64url_encode(secrets.token_bytes(32))


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return _base64url_encode(digest)


def _normalize_resource_host(resource_url: Any) -> str:
    if not isinstance(resource_url, str):
        return QWEN_DEFAULT_RESOURCE_HOST
    trimmed = resource_url.strip()
    if not trimmed:
        return QWEN_DEFAULT_RESOURCE_HOST
    without_scheme = trimmed.replace("https://", "").replace("http://", "")
    host = without_scheme.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()
    return host or QWEN_DEFAULT_RESOURCE_HOST


def _parse_expires_to_datetime(value: Any) -> Optional[datetime]:
    """
    Qwen 导出/接口可能给：
    - 毫秒时间戳（int/str）
    - 秒时间戳（较小的 int/str）
    - ISO8601 字符串
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        if n <= 0:
            return None
        if n > 10_000_000_000:
            return datetime.fromtimestamp(n / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(n, tz=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return _parse_expires_to_datetime(int(s))
        except Exception:
            pass
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(s2)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _safe_parse_json(raw: str) -> Optional[Any]:
    if not isinstance(raw, str):
        return None
    normalized = raw.replace("\ufeff", "").strip()
    if not normalized:
        return None
    try:
        return json.loads(normalized)
    except Exception:
        pass
    first_obj = normalized.find("{")
    last_obj = normalized.rfind("}")
    if 0 <= first_obj < last_obj:
        try:
            return json.loads(normalized[first_obj : last_obj + 1])
        except Exception:
            pass
    first_arr = normalized.find("[")
    last_arr = normalized.rfind("]")
    if 0 <= first_arr < last_arr:
        try:
            return json.loads(normalized[first_arr : last_arr + 1])
        except Exception:
            pass
    return None


def _as_plain_object(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return value


def _first_non_empty_string(*values: Any) -> Optional[str]:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _normalize_imported_qwen_credential(raw: Any) -> Dict[str, Any]:
    root = raw
    if isinstance(root, list):
        if len(root) == 1:
            root = root[0]
        else:
            return {"error": "暂不支持批量导入，请只导入单个账号的 JSON"}

    obj = _as_plain_object(root)
    if obj is None:
        return {"error": "credential_json 解析结果不是对象"}

    nested = (
        _as_plain_object(obj.get("credential"))
        or _as_plain_object(obj.get("token"))
        or _as_plain_object(obj.get("auth"))
        or _as_plain_object(obj.get("data"))
        or None
    )

    typ = _first_non_empty_string(obj.get("type"), (nested or {}).get("type"), obj.get("provider"), (nested or {}).get("provider"))
    access_token = _first_non_empty_string(
        obj.get("access_token"),
        obj.get("accessToken"),
        (nested or {}).get("access_token"),
        (nested or {}).get("accessToken"),
        obj.get("token") if isinstance(obj.get("token"), str) else None,
        (nested or {}).get("token"),
    )
    refresh_token = _first_non_empty_string(
        obj.get("refresh_token"),
        obj.get("refreshToken"),
        (nested or {}).get("refresh_token"),
        (nested or {}).get("refreshToken"),
    )
    email = _first_non_empty_string(obj.get("email"), (nested or {}).get("email"), obj.get("account_email"), obj.get("accountEmail"))
    resource_url = _first_non_empty_string(
        obj.get("resource_url"),
        obj.get("resourceURL"),
        obj.get("resourceUrl"),
        (nested or {}).get("resource_url"),
        (nested or {}).get("resourceURL"),
        (nested or {}).get("resourceUrl"),
    )
    expires_at_candidate = (
        _first_non_empty_string(
            obj.get("expired"),
            obj.get("expiry_date"),
            obj.get("expiry"),
            obj.get("expire"),
            (nested or {}).get("expired"),
            (nested or {}).get("expiry_date"),
            (nested or {}).get("expiry"),
            (nested or {}).get("expire"),
        )
        or obj.get("expires_at")
        or obj.get("expiresAt")
        or (nested or {}).get("expires_at")
        or (nested or {}).get("expiresAt")
        or None
    )
    last_refresh = _first_non_empty_string(
        obj.get("last_refresh"),
        obj.get("lastRefresh"),
        (nested or {}).get("last_refresh"),
        (nested or {}).get("lastRefresh"),
    )

    return {
        "type": typ,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "email": email,
        "resource_url": resource_url,
        "expires_at": expires_at_candidate,
        "last_refresh": last_refresh,
    }


def _account_to_safe_dict(account: QwenAccount) -> Dict[str, Any]:
    return {
        "account_id": account.account_id,
        "user_id": account.user_id,
        "is_shared": int(account.is_shared or 0),
        "status": int(account.status or 0),
        "need_refresh": bool(account.need_refresh),
        "expires_at": int(account.token_expires_at.timestamp() * 1000) if account.token_expires_at else None,
        "email": account.email,
        "account_name": account.account_name,
        "resource_url": account.resource_url,
        "last_refresh": account.last_refresh_at.isoformat() if account.last_refresh_at else None,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


@dataclass
class QwenDeviceFlow:
    device_code: str
    verification_uri_complete: str
    expires_in: int
    interval: int
    code_verifier: str


class QwenAPIService:
    def __init__(self, db: AsyncSession, redis: RedisClient):
        self.db = db
        self.redis = redis

    def _state_key(self, state: str) -> str:
        return f"{QWEN_OAUTH_STATE_KEY_PREFIX}{state}"

    def _load_credentials(self, account: QwenAccount) -> Dict[str, Any]:
        try:
            plaintext = decrypt_api_key(account.credentials)
        except Exception as e:
            raise ValueError(f"Qwen 凭证解密失败: {e}") from e
        try:
            data = json.loads(plaintext)
        except Exception as e:
            raise ValueError(f"Qwen 凭证解析失败: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("Qwen 凭证格式非法：期望 JSON object")
        return data

    def _dump_credentials(self, data: Dict[str, Any]) -> str:
        return encrypt_api_key(json.dumps(data, ensure_ascii=False))

    async def _get_account_by_id(self, account_id: str) -> Optional[QwenAccount]:
        result = await self.db.execute(select(QwenAccount).where(QwenAccount.account_id == account_id))
        return result.scalar_one_or_none()

    async def _get_account_by_email(self, email: str) -> Optional[QwenAccount]:
        result = await self.db.execute(select(QwenAccount).where(QwenAccount.email == email))
        return result.scalar_one_or_none()

    def openai_list_models(self) -> Dict[str, Any]:
        created = int(time.time())
        return {
            "object": "list",
            "data": [
                {"id": "qwen3-coder-plus", "object": "model", "created": created, "owned_by": "qwen"},
                {"id": "qwen3-coder-flash", "object": "model", "created": created, "owned_by": "qwen"},
                {"id": "vision-model", "object": "model", "created": created, "owned_by": "qwen"},
            ],
        }

    async def initiate_device_flow(self) -> QwenDeviceFlow:
        code_verifier = _generate_code_verifier()
        code_challenge = _generate_code_challenge(code_verifier)

        body = {
            "client_id": QWEN_OAUTH_CLIENT_ID,
            "scope": QWEN_OAUTH_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(
                QWEN_OAUTH_DEVICE_CODE_ENDPOINT,
                data=body,
                headers={"Accept": "application/json"},
            )
            raw = resp.text
            data = _safe_parse_json(raw)

            if resp.status_code >= 400:
                err_type = (data or {}).get("error") if isinstance(data, dict) else ""
                err_desc = (data or {}).get("error_description") if isinstance(data, dict) else ""
                raise ValueError(f"Qwen OAuth 发起失败: {err_type} {err_desc or raw}".strip())

            if not isinstance(data, dict):
                raise ValueError("Qwen OAuth 发起失败：响应格式异常（非对象）")

            device_code = _trimmed_str(data.get("device_code"))
            verification_uri_complete = _trimmed_str(data.get("verification_uri_complete"))
            if not device_code or not verification_uri_complete:
                raise ValueError("Qwen OAuth 发起失败：响应缺少 device_code / verification_uri_complete")

            try:
                expires_in = int(data.get("expires_in") or 300)
            except Exception:
                expires_in = 300
            try:
                interval = int(data.get("interval") or 5)
            except Exception:
                interval = 5
            expires_in = max(1, expires_in)
            interval = max(1, interval)

            return QwenDeviceFlow(
                device_code=device_code,
                verification_uri_complete=verification_uri_complete,
                expires_in=expires_in,
                interval=interval,
                code_verifier=code_verifier,
            )

    async def try_exchange_device_flow_token(self, *, device_code: str, code_verifier: str) -> Dict[str, Any]:
        body = {
            "grant_type": QWEN_OAUTH_DEVICE_GRANT_TYPE,
            "client_id": QWEN_OAUTH_CLIENT_ID,
            "device_code": device_code,
            "code_verifier": code_verifier,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(
                QWEN_OAUTH_TOKEN_ENDPOINT,
                data=body,
                headers={"Accept": "application/json"},
            )
            raw = resp.text
            data = _safe_parse_json(raw)

            if 200 <= resp.status_code < 300:
                if not isinstance(data, dict):
                    raise ValueError("Qwen OAuth 登录失败：响应格式异常（非对象）")
                access_token = _trimmed_str(data.get("access_token"))
                if not access_token:
                    raise ValueError("Qwen OAuth 登录失败：响应缺少 access_token")
                return data

            if resp.status_code == 400 and isinstance(data, dict):
                err_type = _trimmed_str(data.get("error"))
                if err_type == "authorization_pending":
                    raise QwenOAuthPendingError()
                if err_type == "slow_down":
                    raise QwenOAuthSlowDownError()
                if err_type == "expired_token":
                    raise ValueError("Qwen OAuth 登录失败：device_code 已过期，请重新开始")
                if err_type == "access_denied":
                    raise ValueError("Qwen OAuth 登录失败：用户拒绝授权，请重新开始")

            err_type = (data or {}).get("error") if isinstance(data, dict) else ""
            err_desc = (data or {}).get("error_description") if isinstance(data, dict) else ""
            raise ValueError(
                f"Qwen OAuth 轮询失败: {err_type} {err_desc or raw or f'HTTP {resp.status_code}'}".strip()
            )

    async def refresh_access_token(self, *, refresh_token: str) -> Dict[str, Any]:
        rt = _trimmed_str(refresh_token)
        if not rt:
            raise ValueError("refresh_token不能为空")

        body = {
            "grant_type": "refresh_token",
            "client_id": QWEN_OAUTH_CLIENT_ID,
            "refresh_token": rt,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(QWEN_OAUTH_TOKEN_ENDPOINT, data=body, headers={"Accept": "application/json"})
            raw = resp.text
            data = _safe_parse_json(raw)

            if resp.status_code >= 400:
                err_type = (data or {}).get("error") if isinstance(data, dict) else ""
                err_desc = (data or {}).get("error_description") if isinstance(data, dict) else ""
                raise ValueError(f"Qwen refresh token 失败: {err_type} {err_desc or raw}".strip())

            if not isinstance(data, dict) or not _trimmed_str(data.get("access_token")):
                raise ValueError("Qwen refresh token 响应缺少 access_token")
            return data

    async def oauth_authorize(self, *, user_id: int, is_shared: int, account_name: Optional[str]) -> Dict[str, Any]:
        if is_shared not in (0, 1):
            raise ValueError("is_shared必须是0或1")

        flow = await self.initiate_device_flow()
        state = f"qwen-{uuid4()}"
        ttl = min(QWEN_OAUTH_STATE_TTL_MAX_SECONDS, max(QWEN_OAUTH_STATE_TTL_MIN_SECONDS, flow.expires_in + 120))
        now_ms = int(time.time() * 1000)

        payload = {
            "provider": "qwen",
            "user_id": int(user_id),
            "is_shared": int(is_shared),
            "account_name": _trimmed_str(account_name),
            "created_at": now_ms,
            "expires_at_ms": now_ms + flow.expires_in * 1000,
            "device_code": flow.device_code,
            "code_verifier": flow.code_verifier,
            "interval": flow.interval,
            "callback_completed": False,
            "completed_at": None,
            "error": None,
            "account_data": None,
        }

        await self.redis.set_json(self._state_key(state), payload, expire=ttl)

        return {
            "success": True,
            "data": {
                "auth_url": flow.verification_uri_complete,
                "state": state,
                "expires_in": flow.expires_in,
                "interval": flow.interval,
            },
        }

    async def oauth_status(self, *, state: str) -> Dict[str, Any]:
        normalized = _trimmed_str(state)
        if not normalized:
            raise QwenAPIError("缺少state参数", status_code=400)

        info = await self.redis.get_json(self._state_key(normalized))
        if not isinstance(info, dict):
            raise QwenAPIError("无效或已过期的state参数", status_code=404)

        if info.get("callback_completed"):
            return {
                "success": True,
                "status": "completed",
                "data": info.get("account_data"),
                "message": "登录已完成",
            }

        err = info.get("error")
        if isinstance(err, str) and err.strip():
            return {"success": False, "status": "failed", "error": err, "message": "登录失败"}

        device_code = _trimmed_str(info.get("device_code"))
        code_verifier = _trimmed_str(info.get("code_verifier"))
        if not device_code or not code_verifier:
            raise QwenAPIError("state 数据不完整（缺少 device_code / code_verifier）", status_code=400)

        try:
            token_data = await self.try_exchange_device_flow_token(device_code=device_code, code_verifier=code_verifier)
        except QwenOAuthPendingError:
            return {"success": True, "status": "pending", "message": "等待用户完成授权..."}
        except QwenOAuthSlowDownError:
            return {"success": True, "status": "pending", "message": "请稍后再试（slow_down）"}
        except Exception as e:
            msg = str(e)
            info["error"] = msg
            await self.redis.set_json(self._state_key(normalized), info, expire=QWEN_OAUTH_STATE_TTL_MAX_SECONDS)
            return {"success": False, "status": "failed", "error": msg, "message": "登录失败"}

        access_token = _trimmed_str(token_data.get("access_token"))
        refresh_token = _trimmed_str(token_data.get("refresh_token")) or None
        resource_url = _normalize_resource_host(token_data.get("resource_url"))

        expires_in_raw = token_data.get("expires_in")
        try:
            expires_in = int(expires_in_raw) if expires_in_raw is not None else 0
        except Exception:
            expires_in = 0
        token_expires_at = _now_utc() + timedelta(seconds=max(expires_in, 0)) if expires_in else None

        is_shared = int(info.get("is_shared") or 0)
        owner_user_id = int(info.get("user_id") or 0)
        if not owner_user_id:
            raise QwenAPIError("state 数据不完整（缺少 user_id）", status_code=400)

        email = f"qwen-{int(time.time())}"
        name = _trimmed_str(info.get("account_name")) or email or "Qwen Account"

        account = await self._upsert_account(
            user_id=owner_user_id,
            is_shared=is_shared,
            account_name=name,
            email=email,
            resource_url=resource_url,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
        )

        safe = _account_to_safe_dict(account)
        info.update({"callback_completed": True, "completed_at": int(time.time() * 1000), "account_data": safe})
        await self.redis.set_json(self._state_key(normalized), info, expire=QWEN_OAUTH_STATE_TTL_MAX_SECONDS)

        return {"success": True, "status": "completed", "data": safe, "message": "登录已完成"}

    async def _upsert_account(
        self,
        *,
        user_id: int,
        is_shared: int,
        account_name: str,
        email: Optional[str],
        resource_url: Optional[str],
        access_token: str,
        refresh_token: Optional[str],
        token_expires_at: Optional[datetime],
    ) -> QwenAccount:
        normalized_email = _trimmed_str(email) or None
        existing = await self._get_account_by_email(normalized_email) if normalized_email else None
        owner_user_id: Optional[int] = None if is_shared == 1 else int(user_id)

        if existing is not None:
            if existing.user_id != owner_user_id:
                raise ValueError(f"该Qwen账号已被其他用户导入: {normalized_email}")

            creds = self._load_credentials(existing)
            creds.update(
                {
                    "type": "qwen",
                    "access_token": access_token,
                    "refresh_token": refresh_token or creds.get("refresh_token"),
                    "resource_url": resource_url,
                    "email": normalized_email,
                    "expires_at_ms": int(token_expires_at.timestamp() * 1000) if token_expires_at else None,
                }
            )
            await self.db.execute(
                update(QwenAccount)
                .where(QwenAccount.account_id == existing.account_id)
                .values(
                    account_name=account_name,
                    is_shared=is_shared,
                    user_id=owner_user_id,
                    status=1,
                    need_refresh=False,
                    email=normalized_email,
                    resource_url=resource_url,
                    token_expires_at=token_expires_at,
                    last_refresh_at=_now_utc(),
                    credentials=self._dump_credentials(creds),
                )
            )
            await self.db.flush()
            updated = await self._get_account_by_id(existing.account_id)
            assert updated is not None
            return updated

        account = QwenAccount(
            account_id=str(uuid4()),
            user_id=owner_user_id,
            account_name=account_name,
            is_shared=is_shared,
            status=1,
            need_refresh=False,
            email=normalized_email,
            resource_url=resource_url,
            token_expires_at=token_expires_at,
            last_refresh_at=_now_utc(),
            credentials=self._dump_credentials(
                {
                    "type": "qwen",
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "resource_url": resource_url,
                    "email": normalized_email,
                    "expires_at_ms": int(token_expires_at.timestamp() * 1000) if token_expires_at else None,
                }
            ),
        )
        self.db.add(account)
        await self.db.flush()
        await self.db.refresh(account)
        return account

    async def import_account(
        self,
        *,
        user_id: int,
        is_shared: int,
        credential_json: str,
        account_name: Optional[str],
    ) -> Dict[str, Any]:
        if is_shared not in (0, 1):
            raise ValueError("is_shared必须是0或1")

        parsed = _safe_parse_json(credential_json)
        if parsed is None:
            raise ValueError("credential_json不是有效JSON")

        normalized = _normalize_imported_qwen_credential(parsed)
        if normalized.get("error"):
            raise ValueError(str(normalized["error"]))

        typ = _trimmed_str(normalized.get("type")).lower()
        if typ and typ != "qwen":
            raise ValueError("只支持type=qwen的凭证文件")

        access_token = _trimmed_str(normalized.get("access_token"))
        if not access_token:
            raise ValueError("缺少access_token")

        refresh_token = _trimmed_str(normalized.get("refresh_token")) or None
        email = _trimmed_str(normalized.get("email")) or None
        resource_url = _normalize_resource_host(normalized.get("resource_url"))
        expires_at_dt = _parse_expires_to_datetime(normalized.get("expires_at"))

        last_refresh_str = _trimmed_str(normalized.get("last_refresh")) or None
        last_refresh_at = _parse_expires_to_datetime(last_refresh_str) if last_refresh_str else None

        name = _trimmed_str(account_name) or email or "Qwen Account"

        account = await self._upsert_account(
            user_id=user_id,
            is_shared=is_shared,
            account_name=name,
            email=email,
            resource_url=resource_url,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at_dt,
        )

        if last_refresh_at is not None:
            await self.db.execute(
                update(QwenAccount)
                .where(QwenAccount.account_id == account.account_id)
                .values(last_refresh_at=last_refresh_at)
            )
            await self.db.flush()
            refreshed = await self._get_account_by_id(account.account_id)
            if refreshed is not None:
                account = refreshed

        return {"success": True, "message": "Qwen账号导入成功", "data": _account_to_safe_dict(account)}

    async def list_accounts(self, *, user_id: int) -> Dict[str, Any]:
        result = await self.db.execute(
            select(QwenAccount).where(QwenAccount.user_id == user_id).order_by(QwenAccount.created_at.desc())
        )
        accounts = result.scalars().all()
        return {"success": True, "data": [_account_to_safe_dict(a) for a in accounts]}

    async def get_account(self, *, user_id: int, account_id: str, is_admin: bool = False) -> Dict[str, Any]:
        account = await self._get_account_by_id(account_id)
        if account is None:
            raise QwenAPIError("账号不存在", status_code=404)
        if not is_admin and account.user_id != user_id:
            raise QwenAPIError("无权访问该账号", status_code=403)
        return {"success": True, "data": _account_to_safe_dict(account)}

    async def export_credentials(self, *, user_id: int, account_id: str, is_admin: bool = False) -> Dict[str, Any]:
        account = await self._get_account_by_id(account_id)
        if account is None:
            raise QwenAPIError("账号不存在", status_code=404)
        if not is_admin and account.user_id != user_id:
            raise QwenAPIError("无权访问该账号", status_code=403)

        creds = self._load_credentials(account)
        export = {
            "type": "qwen",
            "access_token": creds.get("access_token"),
            "refresh_token": creds.get("refresh_token"),
            "expires_at": int(account.token_expires_at.timestamp() * 1000) if account.token_expires_at else None,
            "email": account.email,
            "resource_url": account.resource_url,
            "last_refresh": account.last_refresh_at.isoformat() if account.last_refresh_at else None,
        }
        data = {k: v for k, v in export.items() if v is not None and not (isinstance(v, str) and not v.strip())}
        return {"success": True, "data": data}

    async def update_account_status(
        self,
        *,
        user_id: int,
        account_id: str,
        status: int,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        if status not in (0, 1):
            raise ValueError("status必须是0或1")

        account = await self._get_account_by_id(account_id)
        if account is None:
            raise QwenAPIError("账号不存在", status_code=404)
        if not is_admin and account.user_id != user_id:
            raise QwenAPIError("无权操作该账号", status_code=403)

        await self.db.execute(update(QwenAccount).where(QwenAccount.account_id == account_id).values(status=int(status)))
        await self.db.flush()
        updated = await self._get_account_by_id(account_id)
        assert updated is not None
        return {"success": True, "message": "账号状态已更新", "data": _account_to_safe_dict(updated)}

    async def update_account_name(
        self,
        *,
        user_id: int,
        account_id: str,
        account_name: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        name = _trimmed_str(account_name)
        if not name:
            raise ValueError("account_name不能为空")

        account = await self._get_account_by_id(account_id)
        if account is None:
            raise QwenAPIError("账号不存在", status_code=404)
        if not is_admin and account.user_id != user_id:
            raise QwenAPIError("无权操作该账号", status_code=403)

        await self.db.execute(update(QwenAccount).where(QwenAccount.account_id == account_id).values(account_name=name))
        await self.db.flush()
        updated = await self._get_account_by_id(account_id)
        assert updated is not None
        return {"success": True, "message": "账号名称已更新", "data": _account_to_safe_dict(updated)}

    async def delete_account(self, *, user_id: int, account_id: str, is_admin: bool = False) -> Dict[str, Any]:
        account = await self._get_account_by_id(account_id)
        if account is None:
            raise QwenAPIError("账号不存在", status_code=404)
        if not is_admin and account.user_id != user_id:
            raise QwenAPIError("无权操作该账号", status_code=403)

        await self.db.execute(delete(QwenAccount).where(QwenAccount.account_id == account_id))
        await self.db.flush()
        return {"success": True, "message": "账号已删除"}

    async def _list_available_accounts(self, *, user_id: int, exclude: Optional[set[str]] = None) -> Sequence[QwenAccount]:
        exclude_ids = exclude or set()

        result = await self.db.execute(
            select(QwenAccount)
            .where(
                QwenAccount.status == 1,
                QwenAccount.need_refresh.is_(False),
                QwenAccount.is_shared == 0,
                QwenAccount.user_id == user_id,
            )
            .order_by(QwenAccount.created_at.asc())
        )
        dedicated = [a for a in result.scalars().all() if a.account_id not in exclude_ids]

        result = await self.db.execute(
            select(QwenAccount)
            .where(
                QwenAccount.status == 1,
                QwenAccount.need_refresh.is_(False),
                QwenAccount.is_shared == 1,
                QwenAccount.user_id.is_(None),
            )
            .order_by(QwenAccount.created_at.asc())
        )
        shared = [a for a in result.scalars().all() if a.account_id not in exclude_ids]

        return [*dedicated, *shared]

    async def _mark_need_refresh(self, account: QwenAccount) -> None:
        await self.db.execute(
            update(QwenAccount)
            .where(QwenAccount.account_id == account.account_id)
            .values(need_refresh=True, status=0)
        )
        await self.db.flush()

    def _is_token_expired(self, account: QwenAccount) -> bool:
        if account.token_expires_at is None:
            return True
        return _now_utc() >= account.token_expires_at - timedelta(minutes=5)

    async def _ensure_valid_access_token(self, account: QwenAccount) -> str:
        creds = self._load_credentials(account)
        access_token = _trimmed_str(creds.get("access_token"))
        refresh_token = _trimmed_str(creds.get("refresh_token"))

        if access_token and not self._is_token_expired(account):
            return access_token

        if not refresh_token:
            await self._mark_need_refresh(account)
            raise ValueError("Qwen账号缺少refresh_token，无法刷新")

        token_data = await self.refresh_access_token(refresh_token=refresh_token)
        access_token_new = _trimmed_str(token_data.get("access_token"))
        if not access_token_new:
            await self._mark_need_refresh(account)
            raise ValueError("Qwen refresh token 未返回 access_token")

        expires_in_raw = token_data.get("expires_in")
        try:
            expires_in = int(expires_in_raw) if expires_in_raw is not None else 0
        except Exception:
            expires_in = 0
        token_expires_at = _now_utc() + timedelta(seconds=max(expires_in, 0)) if expires_in else None

        resource_url = _normalize_resource_host(token_data.get("resource_url") or account.resource_url)
        refresh_token_new = _trimmed_str(token_data.get("refresh_token")) or None

        creds.update(
            {
                "access_token": access_token_new,
                "refresh_token": refresh_token_new or creds.get("refresh_token"),
                "resource_url": resource_url,
                "expires_at_ms": int(token_expires_at.timestamp() * 1000) if token_expires_at else None,
            }
        )
        await self.db.execute(
            update(QwenAccount)
            .where(QwenAccount.account_id == account.account_id)
            .values(
                token_expires_at=token_expires_at,
                last_refresh_at=_now_utc(),
                resource_url=resource_url,
                credentials=self._dump_credentials(creds),
                need_refresh=False,
                status=1,
            )
        )
        await self.db.flush()
        return access_token_new

    def _resolve_base_url(self, account: QwenAccount) -> str:
        host = _normalize_resource_host(account.resource_url)
        return f"https://{host}/v1"

    def _build_headers(self, *, access_token: str, stream: bool) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": QWEN_USER_AGENT,
            "X-Goog-Api-Client": QWEN_X_GOOG_API_CLIENT,
            "Client-Metadata": QWEN_CLIENT_METADATA,
            "Accept": "text/event-stream" if stream else "application/json",
        }

    def _sanitize_openai_request_for_qwen(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(request_data or {})

        if isinstance(body.get("max_completion_tokens"), (int, float)) and body.get("max_tokens") is None:
            body["max_tokens"] = body.get("max_completion_tokens")
        body.pop("max_completion_tokens", None)

        body.pop("reasoning_effort", None)
        body.pop("reasoning", None)
        body.pop("image_config", None)

        tools = body.get("tools")
        if tools is None or (isinstance(tools, list) and len(tools) == 0):
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": "do_not_call_me",
                        "description": "Do not call this tool under any circumstances.",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            ]

        if body.get("stream"):
            stream_options = body.get("stream_options")
            merged = dict(stream_options) if isinstance(stream_options, dict) else {}
            merged["include_usage"] = True
            body["stream_options"] = merged

        return body

    async def openai_chat_completions(self, *, user_id: int, request_data: Dict[str, Any]) -> Dict[str, Any]:
        body = self._sanitize_openai_request_for_qwen(request_data)
        body["stream"] = False

        exclude: set[str] = set()
        last_error: Optional[str] = None
        last_status: int = 500

        async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0, connect=30.0)) as client:
            for _ in range(3):
                accounts = await self._list_available_accounts(user_id=user_id, exclude=exclude)
                if not accounts:
                    raise ValueError("没有可用的Qwen账号，请先导入账号")
                account = secrets.choice(list(accounts))
                exclude.add(account.account_id)

                try:
                    access_token = await self._ensure_valid_access_token(account)
                except Exception as e:
                    last_error = str(e)
                    last_status = 400
                    continue

                url = f"{self._resolve_base_url(account).rstrip('/')}/chat/completions"
                resp = await client.post(url, json=body, headers=self._build_headers(access_token=access_token, stream=False))

                if resp.status_code in (401, 403):
                    await self._mark_need_refresh(account)
                    last_error = resp.text[:2000]
                    last_status = resp.status_code
                    continue
                if resp.status_code == 429:
                    last_error = resp.text[:2000] or "Qwen上游错误: 429"
                    last_status = 429
                    continue
                if resp.status_code >= 400:
                    last_error = resp.text[:2000]
                    last_status = resp.status_code
                    break

                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Qwen 上游响应格式异常（非对象）")
                return data

        raise ValueError(last_error or f"Qwen请求失败: HTTP {last_status}")

    async def openai_chat_completions_stream(self, *, user_id: int, request_data: Dict[str, Any]) -> AsyncIterator[bytes]:
        body = self._sanitize_openai_request_for_qwen(request_data)
        body["stream"] = True

        exclude: set[str] = set()
        attempts = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0, connect=30.0)) as client:
            while attempts < 3:
                attempts += 1
                accounts = await self._list_available_accounts(user_id=user_id, exclude=exclude)
                if not accounts:
                    yield 'data: {"error":{"message":"没有可用的Qwen账号，请先导入账号","type":"upstream_error","code":400}}\n\n'.encode(
                        "utf-8"
                    )
                    yield b"data: [DONE]\n\n"
                    return

                account = secrets.choice(list(accounts))
                exclude.add(account.account_id)

                try:
                    access_token = await self._ensure_valid_access_token(account)
                except Exception as e:
                    msg = str(e)
                    yield f"data: {json.dumps({'error': {'message': msg, 'type': 'upstream_error', 'code': 400}}, ensure_ascii=False)}\n\n".encode(
                        "utf-8"
                    )
                    yield b"data: [DONE]\n\n"
                    return

                url = f"{self._resolve_base_url(account).rstrip('/')}/chat/completions"
                headers = self._build_headers(access_token=access_token, stream=True)

                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code in (401, 403):
                        await self._mark_need_refresh(account)
                        continue
                    if resp.status_code == 429:
                        continue
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        msg = text.decode("utf-8", errors="replace")[:2000]
                        yield f"data: {json.dumps({'error': {'message': msg or 'Qwen上游错误', 'type': 'upstream_error', 'code': resp.status_code}}, ensure_ascii=False)}\n\n".encode(
                            "utf-8"
                        )
                        yield b"data: [DONE]\n\n"
                        return

                    async for chunk in resp.aiter_raw():
                        if chunk:
                            yield chunk
                    return

        yield 'data: {"error":{"message":"Qwen请求失败（重试次数已用尽）","type":"upstream_error","code":500}}\n\n'.encode(
            "utf-8"
        )
        yield b"data: [DONE]\n\n"
