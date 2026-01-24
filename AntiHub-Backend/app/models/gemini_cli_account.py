"""
GeminiCLI 账号数据模型

说明：
- 账号归属到 User（user_id），支持同一用户保存多个 GeminiCLI 账号
- 凭证（token 等）使用加密后的 JSON 字符串存储，避免明文落库
- 支持多项目（project_id 逗号分隔）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sqlalchemy import String, Integer, BigInteger, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class GeminiCLIAccount(Base):
    """GeminiCLI 账号模型（落库保存 Google OAuth 凭证与基础信息）"""

    __tablename__ = "gemini_cli_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联的用户ID",
    )

    account_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="账号显示名称",
    )

    status: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="账号状态：0=禁用，1=启用",
    )

    is_shared: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="0=专属账号，1=共享账号（预留）",
    )

    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Google 账号邮箱",
    )

    project_id: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
        comment="GCP Project ID（多项目用逗号分隔）",
    )

    auto_project: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="是否自动选择项目",
    )

    checked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="是否已启用 Cloud AI API",
    )

    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="access_token 过期时间",
    )

    last_refresh_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最后一次刷新 token 的时间",
    )

    credentials: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="加密后的凭证 JSON（包含 access_token/refresh_token 等）",
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

    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最后使用时间",
    )

    user: Mapped["User"] = relationship("User", back_populates="gemini_cli_accounts")

    def __repr__(self) -> str:
        return f"<GeminiCLIAccount(id={self.id}, user_id={self.user_id}, email='{self.email}')>"
