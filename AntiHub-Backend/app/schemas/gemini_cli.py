"""
GeminiCLI 账号相关的 Pydantic Schema

说明：
- 这里的接口由 AntiHub-Backend 直接落库（PostgreSQL）
- 凭证导入/导出使用 JSON 字符串
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GeminiCLIOAuthAuthorizeRequest(BaseModel):
    """生成 GeminiCLI OAuth 登录链接"""

    is_shared: int = Field(0, description="0=专属账号，1=共享账号（预留）")
    account_name: Optional[str] = Field(None, description="账号显示名称（可选）")
    project_id: Optional[str] = Field(None, description="GCP Project ID（可选，留空则自动选择）")


class GeminiCLIOAuthAuthorizeData(BaseModel):
    auth_url: str = Field(..., description="OAuth 授权 URL")
    state: str = Field(..., description="OAuth state，用于回调校验")
    expires_in: int = Field(..., description="state 有效期（秒）")


class GeminiCLIOAuthCallbackRequest(BaseModel):
    """提交 GeminiCLI OAuth 回调 URL（手动粘贴）"""

    callback_url: str = Field(..., description="完整的回调 URL（包含 code/state）")


class GeminiCLIAccountImportRequest(BaseModel):
    """导入 GeminiCLI 账号凭证 JSON"""

    credential_json: str = Field(..., description="GeminiCLI 凭证 JSON")
    is_shared: int = Field(0, description="0=专属账号，1=共享账号（预留）")
    account_name: Optional[str] = Field(None, description="账号显示名称（可选）")


class GeminiCLIAccountUpdateStatusRequest(BaseModel):
    status: int = Field(..., description="0=禁用，1=启用")


class GeminiCLIAccountUpdateNameRequest(BaseModel):
    account_name: str = Field(..., description="账号显示名称")


class GeminiCLIAccountUpdateProjectRequest(BaseModel):
    """更新 GCP Project ID"""

    project_id: Optional[str] = Field(None, description="GCP Project ID（多项目用逗号分隔）")


class GeminiCLIAccountResponse(BaseModel):
    account_id: int = Field(..., alias="id")
    user_id: int
    account_name: str
    status: int
    is_shared: int
    email: Optional[str] = None
    project_id: Optional[str] = None
    auto_project: bool = False
    checked: bool = False
    token_expires_at: Optional[datetime] = None
    last_refresh_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class GeminiCLIAPIResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    data: Optional[Any] = None


class GeminiCLIAccountListResponse(BaseModel):
    success: bool = True
    data: List[GeminiCLIAccountResponse]


class GeminiCLIAccountCredentialsResponse(BaseModel):
    success: bool = True
    data: Dict[str, Any]


class GeminiCLIAccountStatusResponse(BaseModel):
    """OAuth 授权状态（轮询用）"""

    status: str = Field(..., description="pending/success/error")
    message: Optional[str] = None
    account_id: Optional[int] = None
