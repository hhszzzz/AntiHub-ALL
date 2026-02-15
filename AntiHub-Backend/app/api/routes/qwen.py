"""
Qwen 账号管理 API 路由（已合并到 Backend）

说明：
- 账号数据存储在 Backend DB（qwen_accounts）
- OAuth Device Flow 的 state 存储在 Redis，前端通过轮询 /oauth/status 驱动完成登录
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session, get_redis
from app.cache import RedisClient
from app.models.user import User
from app.schemas.qwen import (
    QwenAccountImportRequest,
    QwenAccountUpdateNameRequest,
    QwenAccountUpdateStatusRequest,
    QwenOAuthAuthorizeRequest,
)
from app.services.qwen_api_service import QwenAPIError, QwenAPIService


router = APIRouter(prefix="/api/qwen", tags=["Qwen账号管理"])


def get_qwen_api_service(
    db: AsyncSession = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis),
) -> QwenAPIService:
    return QwenAPIService(db, redis)


def _raise_qwen_api_error(e: QwenAPIError) -> None:
    raise HTTPException(status_code=int(getattr(e, "status_code", 400)), detail=str(e))


@router.post(
    "/oauth/authorize",
    summary="生成 Qwen OAuth 登录链接",
    description="使用 Qwen OAuth Device Flow 生成授权链接，前端轮询 /oauth/status 完成落库。",
)
async def qwen_oauth_authorize(
    request: QwenOAuthAuthorizeRequest,
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.oauth_authorize(
            user_id=current_user.id,
            is_shared=request.is_shared,
            account_name=request.account_name,
        )
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="生成 Qwen OAuth 登录链接失败",
        )


@router.get(
    "/oauth/status/{state}",
    summary="轮询 Qwen OAuth 登录状态",
    description="轮询 Qwen OAuth 登录状态，不返回敏感 token。",
)
async def qwen_oauth_status(
    state: str,
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.oauth_status(state=state)
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="查询 Qwen OAuth 登录状态失败",
        )


@router.post(
    "/accounts/import",
    summary="导入 QwenCli JSON",
    description="将 QwenCli 导出的 JSON 凭证导入到 Backend 数据库中",
)
async def import_qwen_account(
    request: QwenAccountImportRequest,
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.import_account(
            user_id=current_user.id,
            is_shared=request.is_shared,
            credential_json=request.credential_json,
            account_name=request.account_name,
        )
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="导入 Qwen 账号失败",
        )


@router.get(
    "/accounts",
    summary="获取 Qwen 账号列表",
    description="获取当前用户的所有 Qwen 账号",
)
async def list_qwen_accounts(
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.list_accounts(user_id=current_user.id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取 Qwen 账号列表失败",
        )


@router.get(
    "/accounts/{account_id}",
    summary="获取单个 Qwen 账号",
    description="获取指定 Qwen 账号详情（不包含敏感 token）",
)
async def get_qwen_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.get_account(user_id=current_user.id, account_id=account_id)
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取 Qwen 账号失败",
        )


@router.get(
    "/accounts/{account_id}/credentials",
    summary="导出 Qwen 凭证",
    description="导出指定 Qwen 账号保存的凭证信息（敏感），用于前端复制为 JSON",
)
async def get_qwen_account_credentials(
    account_id: str,
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.export_credentials(user_id=current_user.id, account_id=account_id)
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="导出 Qwen 凭证失败",
        )


@router.put(
    "/accounts/{account_id}/status",
    summary="更新 Qwen 账号状态",
    description="启用/禁用 Qwen 账号",
)
async def update_qwen_account_status(
    account_id: str,
    request: QwenAccountUpdateStatusRequest,
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.update_account_status(
            user_id=current_user.id,
            account_id=account_id,
            status=request.status,
        )
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新 Qwen 账号状态失败",
        )


@router.put(
    "/accounts/{account_id}/name",
    summary="更新 Qwen 账号名称",
    description="修改 Qwen 账号显示名称",
)
async def update_qwen_account_name(
    account_id: str,
    request: QwenAccountUpdateNameRequest,
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.update_account_name(
            user_id=current_user.id,
            account_id=account_id,
            account_name=request.account_name,
        )
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新 Qwen 账号名称失败",
        )


@router.delete(
    "/accounts/{account_id}",
    summary="删除 Qwen 账号",
    description="删除指定 Qwen 账号",
)
async def delete_qwen_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    service: QwenAPIService = Depends(get_qwen_api_service),
):
    try:
        return await service.delete_account(user_id=current_user.id, account_id=account_id)
    except QwenAPIError as e:
        _raise_qwen_api_error(e)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除 Qwen 账号失败",
        )

