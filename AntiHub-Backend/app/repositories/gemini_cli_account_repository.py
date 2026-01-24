"""
GeminiCLI 账号数据仓储

约定：
- Repository 层不负责 commit()，事务由调用方（依赖注入的 get_db）统一管理
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gemini_cli_account import GeminiCLIAccount


class GeminiCLIAccountRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_user_id(self, user_id: int) -> Sequence[GeminiCLIAccount]:
        result = await self.db.execute(
            select(GeminiCLIAccount)
            .where(GeminiCLIAccount.user_id == user_id)
            .order_by(GeminiCLIAccount.id.asc())
        )
        return result.scalars().all()

    async def list_enabled_by_user_id(self, user_id: int) -> Sequence[GeminiCLIAccount]:
        """返回"启用"的账号列表（用于路由选择）"""
        result = await self.db.execute(
            select(GeminiCLIAccount)
            .where(GeminiCLIAccount.user_id == user_id, GeminiCLIAccount.status == 1)
            .order_by(GeminiCLIAccount.id.asc())
        )
        return result.scalars().all()

    async def get_by_id(self, account_id: int) -> Optional[GeminiCLIAccount]:
        result = await self.db.execute(select(GeminiCLIAccount).where(GeminiCLIAccount.id == account_id))
        return result.scalar_one_or_none()

    async def get_by_id_and_user_id(self, account_id: int, user_id: int) -> Optional[GeminiCLIAccount]:
        result = await self.db.execute(
            select(GeminiCLIAccount).where(
                GeminiCLIAccount.id == account_id,
                GeminiCLIAccount.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_id_and_email(self, user_id: int, email: str) -> Optional[GeminiCLIAccount]:
        result = await self.db.execute(
            select(GeminiCLIAccount).where(
                GeminiCLIAccount.user_id == user_id,
                GeminiCLIAccount.email == email,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: int,
        account_name: str,
        is_shared: int,
        status: int,
        credentials: str,
        email: Optional[str] = None,
        project_id: Optional[str] = None,
        auto_project: bool = False,
        checked: bool = False,
        token_expires_at: Optional[datetime] = None,
        last_refresh_at: Optional[datetime] = None,
    ) -> GeminiCLIAccount:
        account = GeminiCLIAccount(
            user_id=user_id,
            account_name=account_name,
            is_shared=is_shared,
            status=status,
            credentials=credentials,
            email=email,
            project_id=project_id,
            auto_project=auto_project,
            checked=checked,
            token_expires_at=token_expires_at,
            last_refresh_at=last_refresh_at,
        )

        self.db.add(account)
        await self.db.flush()
        await self.db.refresh(account)
        return account

    async def update_credentials_and_profile(
        self,
        account_id: int,
        user_id: int,
        *,
        account_name: Optional[str] = None,
        credentials: Optional[str] = None,
        email: Optional[str] = None,
        project_id: Optional[str] = None,
        auto_project: Optional[bool] = None,
        checked: Optional[bool] = None,
        token_expires_at: Optional[datetime] = None,
        last_refresh_at: Optional[datetime] = None,
    ) -> Optional[GeminiCLIAccount]:
        values = {}
        if account_name is not None:
            values["account_name"] = account_name
        if credentials is not None:
            values["credentials"] = credentials
        if email is not None:
            values["email"] = email
        if project_id is not None:
            values["project_id"] = project_id
        if auto_project is not None:
            values["auto_project"] = auto_project
        if checked is not None:
            values["checked"] = checked
        if token_expires_at is not None:
            values["token_expires_at"] = token_expires_at
        if last_refresh_at is not None:
            values["last_refresh_at"] = last_refresh_at

        if not values:
            return await self.get_by_id_and_user_id(account_id, user_id)

        await self.db.execute(
            update(GeminiCLIAccount)
            .where(
                GeminiCLIAccount.id == account_id,
                GeminiCLIAccount.user_id == user_id,
            )
            .values(**values)
        )
        await self.db.flush()
        return await self.get_by_id_and_user_id(account_id, user_id)

    async def update_status(
        self, account_id: int, user_id: int, status: int
    ) -> Optional[GeminiCLIAccount]:
        await self.db.execute(
            update(GeminiCLIAccount)
            .where(
                GeminiCLIAccount.id == account_id,
                GeminiCLIAccount.user_id == user_id,
            )
            .values(status=status)
        )
        await self.db.flush()
        return await self.get_by_id_and_user_id(account_id, user_id)

    async def update_name(
        self, account_id: int, user_id: int, account_name: str
    ) -> Optional[GeminiCLIAccount]:
        await self.db.execute(
            update(GeminiCLIAccount)
            .where(
                GeminiCLIAccount.id == account_id,
                GeminiCLIAccount.user_id == user_id,
            )
            .values(account_name=account_name)
        )
        await self.db.flush()
        return await self.get_by_id_and_user_id(account_id, user_id)

    async def update_project(
        self,
        account_id: int,
        user_id: int,
        project_id: Optional[str],
    ) -> Optional[GeminiCLIAccount]:
        await self.db.execute(
            update(GeminiCLIAccount)
            .where(
                GeminiCLIAccount.id == account_id,
                GeminiCLIAccount.user_id == user_id,
            )
            .values(project_id=project_id)
        )
        await self.db.flush()
        return await self.get_by_id_and_user_id(account_id, user_id)

    async def delete(self, account_id: int, user_id: int) -> bool:
        result = await self.db.execute(
            delete(GeminiCLIAccount).where(
                GeminiCLIAccount.id == account_id,
                GeminiCLIAccount.user_id == user_id,
            )
        )
        await self.db.flush()
        return (result.rowcount or 0) > 0

    async def update_last_used_at(self, account_id: int, user_id: int) -> None:
        """更新最后使用时间（用于追踪账号活跃度）"""
        await self.db.execute(
            update(GeminiCLIAccount)
            .where(
                GeminiCLIAccount.id == account_id,
                GeminiCLIAccount.user_id == user_id,
            )
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await self.db.flush()
