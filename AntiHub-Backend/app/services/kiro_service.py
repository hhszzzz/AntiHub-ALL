"""
Kiro账号服务

当前实现：
- 账号管理（/api/kiro/accounts 等）：使用 Backend DB（kiro_accounts）
- 订阅层白名单（/api/kiro/admin/subscription-models）：使用 Backend DB（kiro_subscription_models）
- OpenAI 兼容 /v1/kiro/*：仍保留兼容期的 proxy（通过 PLUGIN_API_BASE_URL 代理到历史 plug-in API）

优化说明：
- 添加 Redis 缓存以减少数据库查询
- plugin_api_key 缓存 TTL 为 60 秒
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
import logging
import json

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import get_redis_client, RedisClient
from app.core.config import get_settings
from app.models.kiro_account import KiroAccount
from app.models.kiro_subscription_model import KiroSubscriptionModel
from app.repositories.plugin_api_key_repository import PluginAPIKeyRepository
from app.utils.encryption import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

# 缓存 TTL（秒）
PLUGIN_API_KEY_CACHE_TTL = 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _trimmed_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return float(default)
    try:
        return float(value)
    except Exception:
        return float(default)


def _to_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _safe_json_load(text_value: Optional[str]) -> Optional[Any]:
    if not isinstance(text_value, str):
        return None
    normalized = text_value.replace("\ufeff", "").strip()
    if not normalized:
        return None
    try:
        return json.loads(normalized)
    except Exception:
        return None


def _account_to_safe_dict(account: KiroAccount) -> Dict[str, Any]:
    return {
        "account_id": account.account_id,
        "user_id": account.user_id,
        "account_name": account.account_name,
        "auth_method": account.auth_method,
        "status": int(account.status or 0),
        "expires_at": _to_ms(account.token_expires_at),
        "email": account.email,
        "subscription": account.subscription,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


class UpstreamAPIError(Exception):
    """上游API错误，用于传递上游服务的错误信息"""
    
    def __init__(
        self,
        status_code: int,
        message: str,
        upstream_response: Optional[Dict[str, Any]] = None
    ):
        self.status_code = status_code
        self.message = message
        self.upstream_response = upstream_response
        # 尝试从上游响应中提取真正的错误消息
        self.extracted_message = self._extract_message()
        super().__init__(self.message)
    
    def _extract_message(self) -> str:
        """从上游响应中提取错误消息"""
        if not self.upstream_response:
            return self.message
        
        # 尝试从 error 字段提取
        error_field = self.upstream_response.get("error")
        if error_field:
            # 如果 error 是字符串，尝试解析其中的 JSON
            if isinstance(error_field, str):
                # 尝试提取 JSON 部分，格式如: "错误: 429 {\"message\":\"...\",\"reason\":null}"
                import re
                json_match = re.search(r'\{.*\}', error_field)
                if json_match:
                    try:
                        inner_json = json.loads(json_match.group())
                        if isinstance(inner_json, dict) and "message" in inner_json:
                            return inner_json["message"]
                    except (json.JSONDecodeError, Exception):
                        pass
                # 如果无法解析 JSON，返回整个 error 字符串
                return error_field
            # 如果 error 是字典
            elif isinstance(error_field, dict):
                if "message" in error_field:
                    return error_field["message"]
                return str(error_field)
        
        # 尝试从 message 字段提取
        if "message" in self.upstream_response:
            return self.upstream_response["message"]
        
        # 尝试从 detail 字段提取
        if "detail" in self.upstream_response:
            return self.upstream_response["detail"]
        
        return self.message


class KiroService:
    """Kiro账号服务类- 通过插件API管理"""
    
    # 支持的Kiro模型列表
    SUPPORTED_MODELS = [
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-20250514",
        "claude-opus-4-5-20251101",
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
    ]
    
    def __init__(self, db: AsyncSession, redis: Optional[RedisClient] = None):
        """
        初始化服务
        
        Args:
            db: 数据库会话
            redis: Redis 客户端（可选，用于缓存）
        """
        self.db = db
        self.settings = get_settings()
        self.plugin_api_key_repo = PluginAPIKeyRepository(db)
        self.base_url = self.settings.plugin_api_base_url
        self.admin_key = self.settings.plugin_api_admin_key
        self._redis = redis
    
    @property
    def redis(self) -> RedisClient:
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis
    
    def _get_cache_key(self, user_id: int) -> str:
        """生成缓存键"""
        return f"plugin_api_key:{user_id}"
    
    async def _get_user_plugin_key(self, user_id: int) -> str:
        """
        获取用户的插件API密钥
        
        优化：使用 Redis 缓存减少数据库查询
        
        Args:
            user_id: 用户ID
            
        Returns:
            解密后的插件API密钥
        """
        cache_key = self._get_cache_key(user_id)
        
        # 尝试从缓存获取
        try:
            cached_key = await self.redis.get(cache_key)
            if cached_key:
                logger.debug(f"从缓存获取 plugin_api_key (kiro): user_id={user_id}")
                return cached_key
        except Exception as e:
            logger.warning(f"Redis 缓存读取失败: {e}")
        
        # 缓存未命中，从数据库获取
        key_record = await self.plugin_api_key_repo.get_by_user_id(user_id)
        if not key_record or not key_record.is_active:
            raise ValueError("用户未配置插件API密钥")
        
        # 解密
        decrypted_key = decrypt_api_key(key_record.api_key)
        
        # 存入缓存
        try:
            await self.redis.set(cache_key, decrypted_key, expire=PLUGIN_API_KEY_CACHE_TTL)
            logger.debug(f"plugin_api_key 已缓存 (kiro): user_id={user_id}, ttl={PLUGIN_API_KEY_CACHE_TTL}s")
        except Exception as e:
            logger.warning(f"Redis 缓存写入失败: {e}")
        
        return decrypted_key
    
    async def _proxy_request(
        self,
        user_id: int,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        代理请求到插件API的Kiro端点
        
        Args:
            user_id: 用户ID
            method: HTTP方法
            path: API路径
            json_data: JSON数据
            params: 查询参数
            
        Returns:
            API响应
            
        Raises:
            UpstreamAPIError: 当上游API返回错误时
        """
        api_key = await self._get_user_plugin_key(user_id)
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                json=json_data,
                params=params,
                headers=headers,
                timeout=1200.0
            )
            
            if response.status_code >= 400:
                # 尝试解析上游错误响应
                upstream_response = None
                try:
                    upstream_response = response.json()
                except Exception:
                    try:
                        upstream_response = {"raw": response.text}
                    except Exception:
                        pass
                
                logger.warning(
                    f"上游API错误: status={response.status_code}, "
                    f"url={url}, response={upstream_response}"
                )
                
                raise UpstreamAPIError(
                    status_code=response.status_code,
                    message=f"上游API返回错误: {response.status_code}",
                    upstream_response=upstream_response
                )
            
            return response.json()
    
    async def _proxy_admin_request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        使用管理员 Key 代理请求到 plug-in API

        用途：全局配置类接口（不绑定具体用户 plug-in key）
        """
        if not self.admin_key:
            raise ValueError("未配置 PLUGIN_API_ADMIN_KEY，无法调用 plug-in 管理接口")

        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.admin_key}"}

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                json=json_data,
                params=params,
                headers=headers,
                timeout=1200.0,
            )

            if response.status_code >= 400:
                upstream_response = None
                try:
                    upstream_response = response.json()
                except Exception:
                    try:
                        upstream_response = {"raw": response.text}
                    except Exception:
                        pass

                logger.warning(
                    f"plug-in admin API错误: status={response.status_code}, url={url}, response={upstream_response}"
                )

                raise UpstreamAPIError(
                    status_code=response.status_code,
                    message=f"plug-in admin API返回错误: {response.status_code}",
                    upstream_response=upstream_response,
                )

            return response.json()

    async def _proxy_stream_request(
        self,
        user_id: int,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None
    ):
        """
        代理流式请求到插件API的Kiro端点
        
        Args:
            user_id: 用户ID
            method: HTTP方法
            path: API路径
            json_data: JSON数据
            
        Yields:
            流式响应数据
            
        Raises:
            UpstreamAPIError: 当上游API返回错误时
        """
        api_key = await self._get_user_plugin_key(user_id)
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                method=method,
                url=url,
                json=json_data,
                headers=headers,
                timeout=httpx.Timeout(1200.0, connect=60.0)
            ) as response:
                if response.status_code >= 400:
                    # 读取错误响应体
                    error_body = await response.aread()
                    upstream_response = None
                    try:
                        upstream_response = json.loads(error_body.decode('utf-8'))
                    except Exception:
                        try:
                            upstream_response = {"raw": error_body.decode('utf-8')}
                        except Exception:
                            upstream_response = {"raw": str(error_body)}
                    
                    logger.warning(
                        f"上游API流式请求错误: status={response.status_code}, "
                        f"url={url}, response={upstream_response}"
                    )
                    
                    raise UpstreamAPIError(
                        status_code=response.status_code,
                        message=f"上游API返回错误: {response.status_code}",
                        upstream_response=upstream_response
                    )
                
                async for chunk in response.aiter_raw():
                    if chunk:
                        yield chunk
    
    #==================== Kiro账号管理 ====================
    
    async def get_oauth_authorize_url(
        self,
        user_id: int,
        provider: str,
        is_shared: int = 0
    ) -> Dict[str, Any]:
        """获取Kiro OAuth授权URL（通过插件API）"""
        return await self._proxy_request(
            user_id=user_id,
            method="POST",
            path="/api/kiro/oauth/authorize",
            json_data={
                "provider": provider,
                "is_shared": is_shared
            }
        )
    
    async def get_oauth_status(self, user_id: int, state: str) -> Dict[str, Any]:
        """轮询Kiro OAuth授权状态（通过插件API）"""
        return await self._proxy_request(
            user_id=user_id,
            method="GET",
            path=f"/api/kiro/oauth/status/{state}"
        )
    
    async def submit_oauth_callback(self, callback_url: str) -> Dict[str, Any]:
        """
        提交 Kiro OAuth 回调（给 AntiHook 用）。

        说明：
        - Kiro OAuth 的 state 信息在 plug-in API 的 authorize 阶段写入 Redis；
        - callback 阶段 plug-in API 本身不要求鉴权（没有用户 token 也能完成），因此这里直接代理即可。
        """
        url = f"{self.base_url}/api/kiro/oauth/callback"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=url,
                json={"callback_url": callback_url},
                timeout=1200.0,
            )

        if response.status_code >= 400:
            upstream_response = None
            try:
                upstream_response = response.json()
            except Exception:
                try:
                    upstream_response = {"raw": response.text}
                except Exception:
                    pass

            logger.warning(
                f"上游API错误: status={response.status_code}, url={url}, response={upstream_response}"
            )

            raise UpstreamAPIError(
                status_code=response.status_code,
                message=f"上游API返回错误: {response.status_code}",
                upstream_response=upstream_response,
            )

        return response.json()

    async def _get_account_by_id(self, account_id: str) -> Optional[KiroAccount]:
        result = await self.db.execute(select(KiroAccount).where(KiroAccount.account_id == account_id))
        return result.scalar_one_or_none()

    def _assert_account_access(self, account: Optional[KiroAccount], user_id: int) -> KiroAccount:
        if account is None:
            raise UpstreamAPIError(status_code=404, message="账号不存在")
        if account.user_id != user_id:
            raise UpstreamAPIError(status_code=403, message="无权访问该账号")
        return account

    def _load_account_credentials(self, account: KiroAccount) -> Dict[str, Any]:
        try:
            plaintext = decrypt_api_key(account.credentials)
            parsed = json.loads(plaintext) if plaintext else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def create_account(self, user_id: int, account_data: Dict[str, Any]) -> Dict[str, Any]:
        """创建/导入 Kiro 账号（Refresh Token）。

        注意：当前实现仅做“落库 + 返回”，不主动请求上游刷新 usage limits。
        """
        refresh_token = _trimmed_str(account_data.get("refresh_token") or account_data.get("refreshToken"))
        if not refresh_token:
            raise UpstreamAPIError(status_code=400, message="missing refresh_token")

        auth_method = _trimmed_str(account_data.get("auth_method") or account_data.get("authMethod") or "Social")
        if auth_method.lower() == "social":
            auth_method = "Social"
        elif auth_method.lower() == "idc":
            auth_method = "IdC"
        if auth_method not in ("Social", "IdC"):
            raise UpstreamAPIError(status_code=400, message="auth_method must be Social or IdC")

        client_id = _trimmed_str(account_data.get("client_id") or account_data.get("clientId"))
        client_secret = _trimmed_str(account_data.get("client_secret") or account_data.get("clientSecret"))
        if auth_method == "IdC" and (not client_id or not client_secret):
            raise UpstreamAPIError(status_code=400, message="IdC requires client_id and client_secret")

        account_name = _trimmed_str(account_data.get("account_name") or account_data.get("accountName")) or "Kiro Account"
        machineid = _trimmed_str(account_data.get("machineid") or account_data.get("machineId")) or None
        region = _trimmed_str(account_data.get("region")) or "us-east-1"
        userid = (
            _trimmed_str(account_data.get("userid") or account_data.get("userId") or account_data.get("user_id"))
            or None
        )
        email = _trimmed_str(account_data.get("email")) or None
        subscription = _trimmed_str(account_data.get("subscription")) or None
        subscription_type = _trimmed_str(account_data.get("subscription_type") or account_data.get("subscriptionType")) or None

        is_shared_raw = account_data.get("is_shared") if "is_shared" in account_data else account_data.get("isShared")
        is_shared = 0
        if isinstance(is_shared_raw, bool):
            is_shared = 1 if is_shared_raw else 0
        elif is_shared_raw is not None:
            try:
                is_shared = int(is_shared_raw)
            except Exception:
                raise UpstreamAPIError(status_code=400, message="is_shared must be 0 or 1")
        if is_shared not in (0, 1):
            raise UpstreamAPIError(status_code=400, message="is_shared must be 0 or 1")

        credentials_payload = {
            "type": "kiro",
            "refresh_token": refresh_token,
            "access_token": account_data.get("access_token") or account_data.get("accessToken"),
            "client_id": client_id or None,
            "client_secret": client_secret or None,
            "profile_arn": account_data.get("profile_arn") or account_data.get("profileArn"),
            "machineid": machineid,
            "region": region,
            "auth_method": auth_method,
            "userid": userid,
            "email": email,
            "subscription": subscription,
            "subscription_type": subscription_type,
        }
        encrypted_credentials = encrypt_api_key(json.dumps(credentials_payload, ensure_ascii=False))

        account = KiroAccount(
            account_id=str(uuid4()),
            user_id=None if is_shared == 1 else int(user_id),
            is_shared=is_shared,
            account_name=account_name,
            auth_method=auth_method,
            region=region,
            machineid=machineid,
            userid=userid,
            email=email,
            subscription=subscription,
            subscription_type=subscription_type,
            status=1,
            need_refresh=False,
            credentials=encrypted_credentials,
        )

        self.db.add(account)
        await self.db.flush()

        created = await self._get_account_by_id(account.account_id)
        assert created is not None
        return {"success": True, "message": "Kiro账号已导入", "data": _account_to_safe_dict(created)}
    
    async def get_accounts(self, user_id: int) -> Dict[str, Any]:
        """获取 Kiro 账号列表（从 Backend DB）。"""
        result = await self.db.execute(
            select(KiroAccount).where(KiroAccount.user_id == user_id).order_by(KiroAccount.created_at.desc())
        )
        accounts = result.scalars().all()
        return {"success": True, "data": [_account_to_safe_dict(a) for a in accounts]}
    
    async def get_account(self, user_id: int, account_id: str) -> Dict[str, Any]:
        """获取单个 Kiro 账号（从 Backend DB）。"""
        account = await self._get_account_by_id(account_id)
        account = self._assert_account_access(account, user_id)
        return {"success": True, "data": _account_to_safe_dict(account)}

    async def get_account_credentials(self, user_id: int, account_id: str) -> Dict[str, Any]:
        """
        导出Kiro账号凭证（敏感信息）

        说明：
        - 仅用于用户自助导出/备份（前端“复制凭证为JSON”）
        - Backend DB 中凭证为加密 JSON；此接口会解密后返回（谨慎使用）
        """
        account = await self._get_account_by_id(account_id)
        account = self._assert_account_access(account, user_id)

        creds = self._load_account_credentials(account)
        export = {
            "type": "kiro",
            "refresh_token": creds.get("refresh_token"),
            "access_token": creds.get("access_token"),
            "client_id": creds.get("client_id"),
            "client_secret": creds.get("client_secret"),
            "profile_arn": creds.get("profile_arn"),
            "machineid": account.machineid or creds.get("machineid"),
            "region": account.region or creds.get("region"),
            "auth_method": account.auth_method or creds.get("auth_method"),
            "expires_at": _to_ms(account.token_expires_at),
            "userid": account.userid,
            "email": account.email,
            "subscription": account.subscription,
            "subscription_type": account.subscription_type,
        }
        data = {k: v for k, v in export.items() if v is not None and not (isinstance(v, str) and not v.strip())}
        return {"success": True, "data": data}
    
    async def update_account_status(
        self,
        user_id: int,
        account_id: str,
        status: int
    ) -> Dict[str, Any]:
        """更新 Kiro 账号状态（从 Backend DB）。"""
        if status not in (0, 1):
            raise UpstreamAPIError(status_code=400, message="status必须是0或1")

        account = await self._get_account_by_id(account_id)
        self._assert_account_access(account, user_id)

        await self.db.execute(update(KiroAccount).where(KiroAccount.account_id == account_id).values(status=int(status)))
        await self.db.flush()

        updated = await self._get_account_by_id(account_id)
        assert updated is not None
        return {"success": True, "message": "账号状态已更新", "data": _account_to_safe_dict(updated)}
    
    async def update_account_name(
        self,
        user_id: int,
        account_id: str,
        account_name: str
    ) -> Dict[str, Any]:
        """更新 Kiro 账号名称（从 Backend DB）。"""
        name = _trimmed_str(account_name)
        if not name:
            raise UpstreamAPIError(status_code=400, message="account_name不能为空")

        account = await self._get_account_by_id(account_id)
        self._assert_account_access(account, user_id)

        await self.db.execute(update(KiroAccount).where(KiroAccount.account_id == account_id).values(account_name=name))
        await self.db.flush()

        updated = await self._get_account_by_id(account_id)
        assert updated is not None
        return {"success": True, "message": "账号名称已更新", "data": _account_to_safe_dict(updated)}
    
    async def get_account_balance(self, user_id: int, account_id: str) -> Dict[str, Any]:
        """获取 Kiro 账号余额（从 Backend DB 的缓存字段计算）。"""
        account = await self._get_account_by_id(account_id)
        account = self._assert_account_access(account, user_id)

        current_usage = _coerce_float(account.current_usage, 0.0)
        usage_limit = _coerce_float(account.usage_limit, 0.0)
        base_available = max(usage_limit - current_usage, 0.0)

        bonus_available = 0.0
        bonus_limit_total = _coerce_float(account.bonus_limit, 0.0)

        bonus_details: List[Dict[str, Any]] = []
        parsed_bonus = _safe_json_load(account.bonus_details)
        if isinstance(parsed_bonus, list):
            for item in parsed_bonus:
                if not isinstance(item, dict):
                    continue
                usage = _coerce_float(item.get("usage"), 0.0)
                limit = _coerce_float(item.get("limit"), 0.0)
                bonus_available += max(limit - usage, 0.0)
                bonus_details.append(item)
            bonus_limit_total = sum(_coerce_float(i.get("limit"), 0.0) for i in bonus_details)
        else:
            bonus_usage = _coerce_float(account.bonus_usage, 0.0)
            bonus_available = max(bonus_limit_total - bonus_usage, 0.0)

        total_limit = usage_limit + bonus_limit_total
        available = base_available + bonus_available

        reset_date = account.reset_date or _now_utc()

        free_trial: Optional[Dict[str, Any]] = None
        if (
            account.free_trial_status is not None
            or account.free_trial_limit is not None
            or account.free_trial_usage is not None
            or account.free_trial_expiry is not None
        ):
            ft_limit = _coerce_float(account.free_trial_limit, 0.0)
            ft_usage = _coerce_float(account.free_trial_usage, 0.0)
            ft_available = max(ft_limit - ft_usage, 0.0)
            ft_expiry = account.free_trial_expiry or _now_utc()
            free_trial = {
                "status": bool(account.free_trial_status) if account.free_trial_status is not None else False,
                "usage": ft_usage,
                "limit": ft_limit,
                "available": ft_available,
                "expiry": ft_expiry.isoformat(),
            }

        data = {
            "account_id": account.account_id,
            "account_name": account.account_name or "Kiro Account",
            "email": account.email or "",
            "subscription": account.subscription or "",
            "subscription_type": account.subscription_type or None,
            "balance": {
                "available": float(available),
                "total_limit": float(total_limit),
                "current_usage": float(current_usage),
                "base_available": float(base_available),
                "bonus_available": float(bonus_available),
                "reset_date": reset_date.isoformat(),
            },
            "free_trial": free_trial,
            "bonus_details": bonus_details,
        }
        return {"success": True, "data": data}
    
    async def get_account_consumption(
        self,
        user_id: int,
        account_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取 Kiro 账号消费记录（当前未迁移历史 kiro_consumption_log，返回空列表）。"""
        account = await self._get_account_by_id(account_id)
        account = self._assert_account_access(account, user_id)

        limit_value = int(limit or 100)
        offset_value = int(offset or 0)
        data = {
            "account_id": account.account_id,
            "account_name": account.account_name or "Kiro Account",
            "logs": [],
            "stats": [],
            "pagination": {"limit": limit_value, "offset": offset_value, "total": 0},
        }
        return {"success": True, "data": data}
    
    async def get_user_consumption_stats(
        self,
        user_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取用户总消费统计（当前未迁移历史数据，返回 0）。"""
        data = {
            "total_requests": "0",
            "total_credit": "0",
            "avg_credit": "0",
        }
        return {"success": True, "data": data}
    
    async def delete_account(self, user_id: int, account_id: str) -> Dict[str, Any]:
        """删除 Kiro 账号（从 Backend DB）。"""
        account = await self._get_account_by_id(account_id)
        self._assert_account_access(account, user_id)

        await self.db.execute(delete(KiroAccount).where(KiroAccount.account_id == account_id))
        await self.db.flush()
        return {"success": True, "message": "账号已删除"}
    
    # ==================== Kiro 订阅层 -> 可用模型（管理员配置） ====================

    async def get_subscription_model_rules(self) -> Dict[str, Any]:
        """获取订阅层可用模型配置（管理员，本地 DB）。"""
        result = await self.db.execute(select(KiroSubscriptionModel))
        rows = result.scalars().all()

        configured: Dict[str, Optional[List[str]]] = {}
        for r in rows:
            models = _safe_json_load(r.allowed_model_ids)
            if isinstance(models, list):
                model_ids = [str(x).strip() for x in models if isinstance(x, (str, int, float)) and str(x).strip()]
                configured[r.subscription] = model_ids
            else:
                configured[r.subscription] = None

        # 收集已出现的 subscription（便于管理员看到实际在用的订阅层）
        subs_result = await self.db.execute(
            select(KiroAccount.subscription).where(KiroAccount.subscription.is_not(None)).distinct()
        )
        subs_from_accounts = [s for s in (subs_result.scalars().all() or []) if isinstance(s, str) and s.strip()]

        all_subs = sorted({*(configured.keys()), *subs_from_accounts})
        data = []
        for sub in all_subs:
            model_ids = configured.get(sub)
            data.append({"subscription": sub, "configured": model_ids is not None, "model_ids": model_ids})

        return {"success": True, "data": data}

    async def upsert_subscription_model_rule(
        self,
        subscription: str,
        model_ids: Optional[List[str]],
    ) -> Dict[str, Any]:
        """设置订阅层可用模型配置（管理员，本地 DB）。

        - model_ids=None：删除配置（回到默认放行）
        """
        sub = _trimmed_str(subscription).upper()
        if not sub:
            raise UpstreamAPIError(status_code=400, message="subscription不能为空")

        if model_ids is None:
            await self.db.execute(delete(KiroSubscriptionModel).where(KiroSubscriptionModel.subscription == sub))
            await self.db.flush()
            return {"success": True, "message": "配置已删除", "data": {"subscription": sub, "configured": False, "model_ids": None}}

        normalized = [str(x).strip() for x in model_ids if isinstance(x, (str, int, float)) and str(x).strip()]
        payload = json.dumps(normalized, ensure_ascii=False)

        existing = await self.db.get(KiroSubscriptionModel, sub)
        if existing is None:
            self.db.add(KiroSubscriptionModel(subscription=sub, allowed_model_ids=payload))
        else:
            await self.db.execute(
                update(KiroSubscriptionModel)
                .where(KiroSubscriptionModel.subscription == sub)
                .values(allowed_model_ids=payload)
            )
        await self.db.flush()

        return {"success": True, "message": "配置已更新", "data": {"subscription": sub, "configured": True, "model_ids": normalized}}

    # ==================== Kiro OpenAI兼容API ====================
    
    async def get_models(self, user_id: int) -> Dict[str, Any]:
        """获取Kiro模型列表（通过插件API）"""
        return await self._proxy_request(
            user_id=user_id,
            method="GET",
            path="/v1/kiro/models"
        )
    
    async def chat_completions(
        self,
        user_id: int,
        request_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Kiro聊天补全（非流式，通过插件API）"""
        return await self._proxy_request(
            user_id=user_id,
            method="POST",
            path="/v1/kiro/chat/completions",
            json_data=request_data
        )
    
    async def chat_completions_stream(
        self,
        user_id: int,
        request_data: Dict[str, Any]
    ):
        """Kiro聊天补全（流式，通过插件API）"""
        async for chunk in self._proxy_stream_request(
            user_id=user_id,
            method="POST",
            path="/v1/kiro/chat/completions",
            json_data=request_data
        ):
            yield chunk
