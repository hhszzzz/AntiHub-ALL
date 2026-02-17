"""
Plugin DB -> Backend DB 迁移状态表

用于记录是否已经完成从旧 AntiHub-plugin DB 导入账号/配额等数据的启动期迁移。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class PluginDbMigrationState(Base):
    __tablename__ = "plugin_db_migration_states"

    # 迁移标识（为后续版本化/多迁移项预留）
    key: Mapped[str] = mapped_column(String(64), primary_key=True)

    # pending / running / done / failed
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="pending",
        comment="migration status: pending|running|done|failed",
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

