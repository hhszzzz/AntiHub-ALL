"""
GeminiCLI 账号服务

功能范围：
- 生成 Google OAuth 登录链接（带 state）
- 解析回调 URL，交换 token
- 执行 Onboarding 流程（loadCodeAssist/onboardUser）
- 启用 cloudaicompanion API
- 导入/导出账号凭证（JSON）
- Token 刷新
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import os
import secrets
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RedisClient
from app.repositories.gemini_cli_account_repository import GeminiCLIAccountRepository
from app.utils.encryption import encrypt_api_key as encrypt_secret
from app.utils.encryption import decrypt_api_key as decrypt_secret

# Google OAuth 配置（使用 Gemini CLI 官方配置）
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Gemini CLI 官方 OAuth 配置（固定值，来自 Gemini CLI 官方实现）
GOOGLE_CLIENT_ID = "77185425430.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-1mdrl61j8kmqUqEdCuCD2t1c-Oo"

# OAuth 回调（兼容 CLIProxyAPI 的 8085 端口）
GOOGLE_REDIRECT_URI = "http://localhost:8085/oauth2callback"

# OAuth Scopes（获取邮箱信息、offline_access 以及支持 onboarding/启用 API 的权限）
# - openid email profile: 获取用户基本信息
# - https://www.googleapis.com/auth/cloudplatformprojects: Cloud Resource Manager API（列出项目）
# - https://www.googleapis.com/auth/service.management: Service Usage API（启用 cloudaicompanion）
OAUTH_SCOPE = "openid email profile https://www.googleapis.com/auth/cloudplatformprojects https://www.googleapis.com/auth/service.management"
OAUTH_SESSION_TTL_SECONDS = 10 * 60

# Gemini CLI (cloudcode-pa) API
CLOUDCODE_PA_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
SERVICE_USAGE_BASE_URL = "https://serviceusage.googleapis.com/v1"

# 必需的 Header
DEFAULT_USER_AGENT = "google-api-nodejs-client/9.15.1"
DEFAULT_X_GOOG_API_CLIENT = "gl-node/22.17.0"
DEFAULT_CLIENT_METADATA = "ideType=IDE_UNSPECIFIED,platform=PLATFORM_UNSPECIFIED,pluginType=GEMINI"

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _generate_state() -> str:
    # gem- 开头的 state，与参考项目一致
    return f"gem-{secrets.token_hex(8)}"


def _parse_oauth_callback(input_str: str) -> Dict[str, str]:
    """
    解析 OAuth 回调 URL（兼容用户粘贴的多种形式）
    """
    trimmed = (input_str or "").strip()
    if not trimmed:
        raise ValueError("callback_url 不能为空")

    # 兼容各种格式的输入
    candidate = trimmed
    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = "http://localhost" + candidate
        elif "=" in candidate:
            candidate = "http://localhost/?" + candidate
        else:
            raise ValueError("callback_url 不是合法的 URL 或 query")

    parsed = urlparse(candidate)
    q = parse_qs(parsed.query)

    code = (q.get("code", [""])[0] or "").strip()
    state = (q.get("state", [""])[0] or "").strip()
    err = (q.get("error", [""])[0] or "").strip()
    err_desc = (q.get("error_description", [""])[0] or "").strip()

    if not err and err_desc:
        err = err_desc

    if not code and not err:
        raise ValueError("callback_url 缺少 code")
    if not state:
        raise ValueError("callback_url 缺少 state")

    return {"code": code, "state": state, "error": err, "error_description": err_desc}


def _default_account_name(email: Optional[str]) -> str:
    """默认账号名称：邮箱前缀"""
    if not email:
        return "GeminiCLI Account"
    local = email.split("@", 1)[0]
    return f"gemini-{local[:8]}" if len(local) > 8 else f"gemini-{local}"


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """解析 ISO8601 格式的日期时间字符串"""
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    s = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class GeminiCLIService:
    def __init__(self, db: AsyncSession, redis: RedisClient):
        self.db = db
        self.redis = redis
        self.repo = GeminiCLIAccountRepository(db)

    async def create_oauth_authorize_url(
        self,
        user_id: int,
        *,
        is_shared: int = 0,
        account_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        生成 Google OAuth 登录链接

        关键参数：
        - access_type=offline: 获取 refresh_token
        - prompt=consent: 强制弹出同意屏幕
        """
        if is_shared not in (0, 1):
            raise ValueError("is_shared 必须是 0 或 1")

        state = _generate_state()

        params = {
            "client_id": GOOGLE_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "state": state,
            "access_type": "offline",  # 关键：获取 refresh_token
            "prompt": "consent",  # 关键：确保返回 refresh_token
        }

        auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

        expires_in = OAUTH_SESSION_TTL_SECONDS
        now = _now_utc()
        session_payload = {
            "user_id": user_id,
            "is_shared": is_shared,
            "account_name": (account_name or "").strip() or None,
            "project_id": (project_id or "").strip() or None,
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=expires_in)),
        }
        await self.redis.set_json(
            f"gemini_cli_oauth:{state}",
            session_payload,
            expire=expires_in,
        )

        return {
            "success": True,
            "data": {
                "auth_url": auth_url,
                "state": state,
                "expires_in": expires_in,
            },
        }

    async def submit_oauth_callback(
        self,
        user_id: int,
        callback_url: str,
    ) -> Dict[str, Any]:
        """
        提交 OAuth 回调 URL 并落库

        流程：
        1. 解析回调 URL 获取 code 和 state
        2. 验证 state
        3. 用 code 换取 token
        4. 获取用户信息
        5. 执行 onboarding（如果指定了 project_id）
        6. 落库保存
        """
        parsed = _parse_oauth_callback(callback_url)
        state = parsed["state"]
        code = parsed["code"]
        err = parsed["error"]
        if err:
            raise ValueError(f"OAuth 登录失败: {err}")

        # 验证 state
        key = f"gemini_cli_oauth:{state}"
        session = await self.redis.get_json(key)
        if not session:
            raise ValueError("state 不存在或已过期，请重新生成登录链接")
        if int(session.get("user_id") or 0) != int(user_id):
            raise ValueError("state 不属于当前用户")

        # 交换 token
        token_resp = await self._exchange_code_for_tokens(code)

        now = _now_utc()
        expires_in = int(token_resp.get("expires_in") or 3600)
        expires_at = now + timedelta(seconds=expires_in)

        # 获取用户信息
        access_token = (token_resp.get("access_token") or "").strip()
        userinfo = await self._get_userinfo(access_token)
        email = userinfo.get("email", "")

        # 存储 token（转换为 map 格式）
        storage_payload = {
            "access_token": access_token,
            "refresh_token": (token_resp.get("refresh_token") or "").strip(),
            "token_type": token_resp.get("token_type", "Bearer"),
            "expires_at": _iso(expires_at),
            "issued_at": _iso(now),
        }
        encrypted_credentials = encrypt_secret(
            json.dumps(storage_payload, ensure_ascii=False)
        )

        account_name = (session.get("account_name") or "").strip()
        if not account_name:
            account_name = _default_account_name(email)

        project_id = (session.get("project_id") or "").strip() or None

        # 检查是否已存在
        existing = await self.repo.get_by_user_id_and_email(user_id, email)

        # 执行 onboarding（如果指定了 project_id）
        auto_project = False
        checked = False

        if project_id:
            try:
                await self._perform_onboarding(
                    access_token,
                    project_id,
                )
                checked = True
            except Exception as e:
                logger.warning(
                    "gemini_cli onboarding failed: email=%s project=%s error=%s",
                    email,
                    project_id,
                    str(e),
                )
                # onboarding 失败不影响落库，只是 checked=False

        if existing:
            updated = await self.repo.update_credentials_and_profile(
                existing.id,
                user_id,
                account_name=account_name,
                credentials=encrypted_credentials,
                email=email,
                project_id=project_id,
                auto_project=auto_project,
                checked=checked,
                token_expires_at=expires_at,
                last_refresh_at=now,
            )
            account = updated or existing
        else:
            account = await self.repo.create(
                user_id=user_id,
                account_name=account_name,
                is_shared=int(session.get("is_shared") or 0),
                status=1,
                credentials=encrypted_credentials,
                email=email,
                project_id=project_id,
                auto_project=auto_project,
                checked=checked,
                token_expires_at=expires_at,
                last_refresh_at=now,
            )

        # 消耗 state
        await self.redis.delete(key)

        return {"success": True, "data": account}

    async def import_account(
        self,
        user_id: int,
        *,
        credential_json: str,
        is_shared: int = 0,
        account_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        导入 GeminiCLI 账号凭证 JSON

        格式参考：
        {
            "access_token": "...",
            "refresh_token": "...",
            "email": "...",
            "project_id": "...",
            ...
        }
        """
        if is_shared not in (0, 1):
            raise ValueError("is_shared 必须是 0 或 1")

        raw = (credential_json or "").strip()
        if not raw:
            raise ValueError("credential_json 不能为空")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError("credential_json 不是合法 JSON") from e
        if not isinstance(payload, dict):
            raise ValueError("credential_json 必须是 JSON object")

        access_token = (payload.get("access_token") or "").strip()
        refresh_token = (payload.get("refresh_token") or "").strip()
        email = (payload.get("email") or "").strip() or None
        project_id = (payload.get("project_id") or "").strip() or None

        # 解析 token 过期时间
        token_expires_at = None
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float, str)):
            try:
                expires_in_seconds = int(expires_in)
                if expires_in_seconds > 0:
                    token_expires_at = _now_utc() + timedelta(seconds=expires_in_seconds)
            except (ValueError, TypeError):
                pass

        # 如果没有 expires_in，尝试解析 expires_at/expired 字段
        if token_expires_at is None:
            expires_at_str = (
                payload.get("expires_at")
                or payload.get("expired")
                or payload.get("expiry")
                or ""
            )
            if isinstance(expires_at_str, str):
                token_expires_at = _parse_iso_datetime(expires_at_str)
            elif isinstance(expires_at_str, (int, float)):
                try:
                    # Unix timestamp
                    token_expires_at = datetime.fromtimestamp(
                        int(expires_at_str), tz=timezone.utc
                    )
                except (ValueError, TypeError, OSError):
                    pass

        if not access_token and not refresh_token:
            raise ValueError(
                "credential_json 缺少有效凭证字段（access_token/refresh_token）"
            )

        # 规范化并加密存储
        normalized = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": payload.get("token_type", "Bearer"),
            "email": email or "",
            "project_id": project_id or "",
        }
        encrypted_credentials = encrypt_secret(
            json.dumps(normalized, ensure_ascii=False)
        )

        final_name = (account_name or "").strip()
        if not final_name:
            final_name = _default_account_name(email)

        existing = None
        if email:
            existing = await self.repo.get_by_user_id_and_email(user_id, email)

        if existing:
            updated = await self.repo.update_credentials_and_profile(
                existing.id,
                user_id,
                account_name=final_name,
                credentials=encrypted_credentials,
                email=email,
                project_id=project_id,
                token_expires_at=token_expires_at,
            )
            account = updated or existing
        else:
            account = await self.repo.create(
                user_id=user_id,
                account_name=final_name,
                is_shared=is_shared,
                status=1,
                credentials=encrypted_credentials,
                email=email,
                project_id=project_id,
                token_expires_at=token_expires_at,
            )

        return {"success": True, "data": account}

    async def list_accounts(self, user_id: int) -> Dict[str, Any]:
        accounts = await self.repo.list_by_user_id(user_id)
        return {"success": True, "data": list(accounts)}

    async def get_account(
        self,
        user_id: int,
        account_id: int,
    ) -> Dict[str, Any]:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        return {"success": True, "data": account}

    async def export_account_credentials(
        self,
        user_id: int,
        account_id: int,
    ) -> Dict[str, Any]:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        decrypted = decrypt_secret(account.credentials)
        try:
            credential_obj = json.loads(decrypted)
        except Exception:
            credential_obj = {"raw": decrypted}
        return {"success": True, "data": credential_obj}

    async def update_account_status(
        self,
        user_id: int,
        account_id: int,
        status: int,
    ) -> Dict[str, Any]:
        if status not in (0, 1):
            raise ValueError("status 必须是 0 或 1")
        account = await self.repo.update_status(account_id, user_id, status)
        if not account:
            raise ValueError("账号不存在")
        return {"success": True, "data": account}

    async def update_account_name(
        self,
        user_id: int,
        account_id: int,
        account_name: str,
    ) -> Dict[str, Any]:
        name = (account_name or "").strip()
        if not name:
            raise ValueError("account_name 不能为空")
        account = await self.repo.update_name(account_id, user_id, name)
        if not account:
            raise ValueError("账号不存在")
        return {"success": True, "data": account}

    async def update_account_project(
        self,
        user_id: int,
        account_id: int,
        project_id: Optional[str],
    ) -> Dict[str, Any]:
        account = await self.repo.update_project(account_id, user_id, project_id)
        if not account:
            raise ValueError("账号不存在")
        return {"success": True, "data": account}

    async def delete_account(
        self,
        user_id: int,
        account_id: int,
    ) -> Dict[str, Any]:
        ok = await self.repo.delete(account_id, user_id)
        if not ok:
            raise ValueError("账号不存在")
        return {"success": True, "data": {"deleted": True}}

    async def _exchange_code_for_tokens(
        self,
        code: str,
    ) -> Dict[str, Any]:
        """用授权码换取 access_token 和 refresh_token"""
        form = {
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data=form,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )

        if resp.status_code != 200:
            raise ValueError(f"token 交换失败: HTTP {resp.status_code}")

        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("token 响应格式异常")

        if "error" in data:
            err_desc = data.get("error_description", data.get("error"))
            raise ValueError(f"OAuth 错误: {err_desc}")

        return data

    async def _get_userinfo(self, access_token: str) -> Dict[str, Any]:
        """获取 Google 用户信息"""
        url = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            logger.warning("get userinfo failed: HTTP %s", resp.status_code)
            return {}

        return resp.json()

    async def _perform_onboarding(
        self,
        access_token: str,
        project_id: str,
    ) -> None:
        """
        执行 Gemini CLI Onboarding 流程

        步骤：
        1. loadCodeAssist - 加载代码助手
        2. onboardUser - 用户入驻（可能需要轮询）
        3. enableCloudAIAPI - 启用 Cloud AI API
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            "X-Goog-Api-Client": DEFAULT_X_GOOG_API_CLIENT,
            "Client-Metadata": DEFAULT_CLIENT_METADATA,
        }

        # 1. loadCodeAssist
        await self._call_load_code_assist(headers, project_id)

        # 2. onboardUser
        await self._call_onboard_user(headers, project_id)

        # 3. enable Cloud AI API
        await self._enable_cloud_ai_api(access_token, project_id)

    async def _call_load_code_assist(
        self,
        headers: Dict[str, str],
        project_id: str,
    ) -> None:
        """调用 loadCodeAssist 接口"""
        url = f"{CLOUDCODE_PA_BASE_URL}:loadCodeAssist"
        body = {
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            },
            "cloudaicompanionProject": project_id,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code not in (200, 204):
            logger.warning(
                "loadCodeAssist failed: HTTP %s, response: %s",
                resp.status_code,
                resp.text[:500],
            )

    async def _call_onboard_user(
        self,
        headers: Dict[str, str],
        project_id: str,
    ) -> None:
        """
        调用 onboardUser 接口

        默认使用 default tier，可能需要轮询等待完成
        """
        url = f"{CLOUDCODE_PA_BASE_URL}:onboardUser"
        body = {
            "tierId": "default",
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            },
            "cloudaicompanionProject": project_id,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code not in (200, 204):
            logger.warning(
                "onboardUser failed: HTTP %s, response: %s",
                resp.status_code,
                resp.text[:500],
            )
            return

        # 检查是否需要轮询
        data = resp.json() if resp.status_code == 200 else {}
        if not data.get("done", False):
            # 可以在这里实现轮询逻辑，但为简化先跳过
            logger.info("onboardUser not done, skipping polling")

    async def _enable_cloud_ai_api(
        self,
        access_token: str,
        project_id: str,
    ) -> None:
        """启用 cloudaicompanion.googleapis.com API"""
        service_name = "cloudaicompanion.googleapis.com"
        url = f"{SERVICE_USAGE_BASE_URL}/projects/{project_id}/services/{service_name}:enable"
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers)

        if resp.status_code not in (200, 204):
            logger.warning(
                "enable Cloud AI API failed: HTTP %s, response: %s",
                resp.status_code,
                resp.text[:500],
            )

    def _load_account_credentials(self, account: Any) -> Dict[str, Any]:
        """加载账号凭证（解密）"""
        decrypted = decrypt_secret(account.credentials)
        try:
            obj = json.loads(decrypted)
        except Exception:
            obj = {}
        return obj if isinstance(obj, dict) else {}

    async def _try_refresh_account(
        self,
        account: Any,
        creds: Dict[str, Any],
    ) -> bool:
        """尝试刷新 access_token"""
        refresh_token = creds.get("refresh_token", "").strip()
        if not refresh_token:
            return False

        try:
            form = {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data=form,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                )

            if resp.status_code != 200:
                return False

            data = resp.json()
            if "error" in data:
                return False

            now = _now_utc()
            expires_in = int(data.get("expires_in") or 3600)
            expires_at = now + timedelta(seconds=expires_in)

            # 更新凭证
            storage_payload = {
                "access_token": (data.get("access_token") or "").strip(),
                "refresh_token": (
                    data.get("refresh_token") or refresh_token
                ).strip(),
                "token_type": data.get("token_type", "Bearer"),
                "expires_at": _iso(expires_at),
                "issued_at": _iso(now),
            }

            encrypted_credentials = encrypt_secret(
                json.dumps(storage_payload, ensure_ascii=False)
            )

            await self.repo.update_credentials_and_profile(
                account.id,
                account.user_id,
                credentials=encrypted_credentials,
                token_expires_at=expires_at,
                last_refresh_at=now,
            )
            await self.db.flush()
            await self.db.commit()
            return True

        except Exception as e:
            logger.warning(
                "refresh gemini_cli token failed: account_id=%s error=%s",
                account.id,
                str(e),
            )
            return False

    async def get_valid_access_token(
        self,
        user_id: int,
        account_id: int,
    ) -> str:
        """
        获取有效的 access_token（自动刷新）

        用于运行时调用 Gemini CLI 接口

        支持仅包含 refresh_token 的账号（会自动刷新）
        """
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")

        creds = self._load_account_credentials(account)

        # 检查是否需要刷新
        now = _now_utc()
        expires_at = account.token_expires_at
        need_refresh = False

        # 情况1: access_token 为空或缺失 - 强制刷新
        access_token = creds.get("access_token", "").strip()
        if not access_token:
            need_refresh = True
        # 情况2: access_token 即将过期 - 提前刷新
        elif isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            # 提前 60 秒刷新
            if expires_at <= now + timedelta(seconds=60):
                need_refresh = True

        if need_refresh:
            refreshed = await self._try_refresh_account(account, creds)
            if not refreshed:
                # 刷新失败，尝试使用现有的 access_token
                access_token = creds.get("access_token", "").strip()
                if not access_token:
                    raise ValueError("无法获取有效的 access_token（刷新失败且无可用 token）")
            # 重新加载
            account = await self.repo.get_by_id_and_user_id(account_id, user_id)
            creds = self._load_account_credentials(account)

        access_token = creds.get("access_token", "").strip()
        if not access_token:
            raise ValueError("账号缺少 access_token")

        return access_token
