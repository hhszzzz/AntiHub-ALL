"""add_qwen_accounts_table

Revision ID: 3f9c2d4e6b1a
Revises: 2b6c1a1f7c3e
Create Date: 2026-02-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f9c2d4e6b1a"
down_revision: Union[str, None] = "2b6c1a1f7c3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qwen_accounts",
        sa.Column("account_id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("is_shared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("need_refresh", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("resource_url", sa.String(length=255), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("is_shared IN (0, 1)", name="ck_qwen_accounts_is_shared"),
        sa.CheckConstraint("status IN (0, 1)", name="ck_qwen_accounts_status"),
    )
    op.create_index("ix_qwen_accounts_user_id", "qwen_accounts", ["user_id"])
    op.create_index("ix_qwen_accounts_email", "qwen_accounts", ["email"])


def downgrade() -> None:
    op.drop_table("qwen_accounts")
