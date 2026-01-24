"""add_gemini_cli_accounts_table

Revision ID: f1b2c3d4e5f6
Revises: e8f1a2b3c4d5
Create Date: 2026-01-23

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1b2c3d4e5f6"
down_revision: Union[str, None] = "e8f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gemini_cli_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_shared", sa.Integer(), server_default="0", nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("project_id", sa.String(length=1024), nullable=True),
        sa.Column("auto_project", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("checked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        op.f("ix_gemini_cli_accounts_user_id"), "gemini_cli_accounts", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_gemini_cli_accounts_email"), "gemini_cli_accounts", ["email"], unique=False
    )
    op.create_index(
        op.f("ix_gemini_cli_accounts_status"), "gemini_cli_accounts", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_gemini_cli_accounts_status"), table_name="gemini_cli_accounts")
    op.drop_index(op.f("ix_gemini_cli_accounts_email"), table_name="gemini_cli_accounts")
    op.drop_index(op.f("ix_gemini_cli_accounts_user_id"), table_name="gemini_cli_accounts")
    op.drop_table("gemini_cli_accounts")
