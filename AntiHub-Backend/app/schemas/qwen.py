"""
Qwen 账号相关的数据模型

说明：
- 账号数据存储在 Backend 数据库（qwen_accounts），凭证以加密 JSON 形式落库。
- OAuth Device Flow 的 state 存储在 Redis；前端通过轮询 /api/qwen/oauth/status/{state} 驱动完成登录。
"""

from typing import Optional
from pydantic import BaseModel, Field


class QwenAccountImportRequest(BaseModel):
    """导入 QwenCli 导出的 JSON 凭证"""

    credential_json: str = Field(..., description="QwenCli 导出的 JSON 字符串")
    is_shared: int = Field(0, description="0=专属账号，1=共享账号")
    account_name: Optional[str] = Field(None, description="账号显示名称（可选）")


class QwenAccountUpdateStatusRequest(BaseModel):
    """更新 Qwen 账号状态"""

    status: int = Field(..., description="0=禁用，1=启用")


class QwenAccountUpdateNameRequest(BaseModel):
    """更新 Qwen 账号名称"""

    account_name: str = Field(..., description="账号显示名称")


class QwenOAuthAuthorizeRequest(BaseModel):
    """生成 Qwen OAuth（Device Flow）授权链接"""

    is_shared: int = Field(0, description="0=专属账号，1=共享账号")
    account_name: Optional[str] = Field(None, description="账号显示名称（可选）")
