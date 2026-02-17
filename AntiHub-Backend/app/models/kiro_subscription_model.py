"""
Kiro 订阅层 -> 可用模型白名单配置（管理员配置）

说明：
- 迁移自 AntiHub-plugin public.kiro_subscription_models
- 用于限制不同 subscription 可用的模型列表（可选；未配置时视为默认放行）
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class KiroSubscriptionModel(Base):
    __tablename__ = "kiro_subscription_models"

    subscription: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        comment="订阅层名称（例如 KIRO FREE / KIRO PRO+）",
    )

    allowed_model_ids: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="允许的模型ID列表 JSON（null 表示未配置/默认放行）",
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

    def __repr__(self) -> str:
        return f"<KiroSubscriptionModel(subscription='{self.subscription}')>"

