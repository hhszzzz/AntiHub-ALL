"""
Qwen 账号数据模型（原 AntiHub-plugin public.qwen_accounts）

说明：
- 账号可为“专属”（is_shared=0，绑定 user_id）或“共享”（is_shared=1，user_id 为空）
- 凭证（access_token/refresh_token 等）使用加密后的 JSON 字符串存储，避免明文落库
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class QwenAccount(Base):
    __tablename__ = "qwen_accounts"

    account_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="账号ID（兼容 plugin 端 uuid account_id）",
    )

    user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="关联的用户ID（共享账号为空）",
    )

    account_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="账号显示名称（可选）",
    )

    is_shared: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        comment="0=专属账号，1=共享账号",
    )

    status: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        comment="账号状态：0=禁用，1=启用",
    )

    need_refresh: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="是否需要重新授权/刷新（通常由 refresh_token 失效触发）",
    )

    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="账号邮箱（可选，用于幂等导入）",
    )

    resource_url: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Qwen resource host（例如 portal.qwen.ai，可选）",
    )

    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="access_token 过期时间（可选）",
    )

    last_refresh_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最后一次刷新 token 的时间（可选）",
    )

    credentials: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="加密后的凭证 JSON（包含 access_token/refresh_token/expires_at_ms/resource_url/email 等）",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )

    user: Mapped[Optional["User"]] = relationship("User", back_populates="qwen_accounts")

    def __repr__(self) -> str:
        return f"<QwenAccount(account_id='{self.account_id}', user_id={self.user_id}, is_shared={self.is_shared})>"

