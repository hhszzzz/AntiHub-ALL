"""
Kiro 账号数据模型（原 AntiHub-plugin public.kiro_accounts）

说明：
- 账号数据存储在 Backend DB（kiro_accounts）
- 凭证（refresh_token/access_token/client_secret 等）使用加密后的 JSON 字符串存储，避免明文落库
- usage limits / bonus / free_trial 等展示字段按插件时期字段保留（尽量保持 UI 行为）
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class KiroAccount(Base):
    __tablename__ = "kiro_accounts"

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

    auth_method: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
        comment="认证方式：Social/IdC（可选）",
    )

    region: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="AWS region（可选，默认 us-east-1）",
    )

    machineid: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        comment="Kiro machineid（可选）",
    )

    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="账号邮箱（可选，用于展示/幂等导入）",
    )

    userid: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Kiro upstream userid（字段名对齐 plugin：userid）",
    )

    subscription: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="订阅层（如 KIRO PRO+，可选）",
    )

    subscription_type: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="订阅层类型（更细粒度枚举，可选）",
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

    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="access_token 过期时间（可选）",
    )

    current_usage: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="当前使用量（可选，来源 usage limits）",
    )

    usage_limit: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="基础总额度（可选，来源 usage limits）",
    )

    reset_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="重置日期（可选，来源 usage limits）",
    )

    bonus_usage: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="bonus 已使用量（可选，来源 usage limits）",
    )

    bonus_limit: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="bonus 总额度（可选，来源 usage limits）",
    )

    bonus_details: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="bonus 详情 JSON（可选）",
    )

    free_trial_status: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
        comment="免费试用状态（可选）",
    )

    free_trial_usage: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="免费试用已使用量（可选）",
    )

    free_trial_limit: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="免费试用总额度（可选）",
    )

    free_trial_expiry: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="免费试用过期时间（可选）",
    )

    credentials: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="加密后的凭证 JSON（至少包含 refresh_token；如有 access_token/client_id/client_secret/profile_arn 也放入）",
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
        comment="最后使用时间（预留）",
    )

    user: Mapped[Optional["User"]] = relationship("User", back_populates="kiro_accounts")

    def __repr__(self) -> str:
        return f"<KiroAccount(account_id='{self.account_id}', user_id={self.user_id}, is_shared={self.is_shared})>"

