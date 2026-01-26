"""
ZAI TTS 账号相关的数据模型
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ZaiTTSAccountCreateRequest(BaseModel):
    account_name: str = Field(..., description="账号名称")
    zai_user_id: str = Field(..., description="ZAI 用户ID")
    token: str = Field(..., description="ZAI Token")
    voice_id: str = Field("system_001", description="默认音色ID")


class ZaiTTSAccountUpdateStatusRequest(BaseModel):
    status: int = Field(..., description="0=禁用，1=启用")


class ZaiTTSAccountUpdateNameRequest(BaseModel):
    account_name: str = Field(..., description="账号名称")


class ZaiTTSAccountUpdateCredentialsRequest(BaseModel):
    zai_user_id: Optional[str] = Field(None, description="ZAI 用户ID")
    token: Optional[str] = Field(None, description="ZAI Token")
    voice_id: Optional[str] = Field(None, description="默认音色ID")


class ZaiTTSAccountResponse(BaseModel):
    account_id: int = Field(..., alias="id")
    account_name: str
    status: int
    zai_user_id: str
    voice_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "populate_by_name": True}
