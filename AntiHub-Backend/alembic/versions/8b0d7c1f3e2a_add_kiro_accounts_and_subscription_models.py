"""add_kiro_accounts_and_subscription_models

Revision ID: 8b0d7c1f3e2a
Revises: 3f9c2d4e6b1a
Create Date: 2026-02-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8b0d7c1f3e2a"
down_revision: Union[str, None] = "3f9c2d4e6b1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kiro_accounts",
        sa.Column("account_id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("auth_method", sa.String(length=16), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("machineid", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("userid", sa.String(length=255), nullable=True),
        sa.Column("subscription", sa.String(length=255), nullable=True),
        sa.Column("subscription_type", sa.String(length=255), nullable=True),
        sa.Column("is_shared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("need_refresh", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_usage", sa.Float(), nullable=True),
        sa.Column("usage_limit", sa.Float(), nullable=True),
        sa.Column("reset_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bonus_usage", sa.Float(), nullable=True),
        sa.Column("bonus_limit", sa.Float(), nullable=True),
        sa.Column("bonus_details", sa.Text(), nullable=True),
        sa.Column("free_trial_status", sa.Boolean(), nullable=True),
        sa.Column("free_trial_usage", sa.Float(), nullable=True),
        sa.Column("free_trial_limit", sa.Float(), nullable=True),
        sa.Column("free_trial_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("is_shared IN (0, 1)", name="ck_kiro_accounts_is_shared"),
        sa.CheckConstraint("status IN (0, 1)", name="ck_kiro_accounts_status"),
    )
    op.create_index("ix_kiro_accounts_user_id", "kiro_accounts", ["user_id"])
    op.create_index("ix_kiro_accounts_email", "kiro_accounts", ["email"])

    op.create_table(
        "kiro_subscription_models",
        sa.Column("subscription", sa.String(length=255), nullable=False, primary_key=True),
        sa.Column("allowed_model_ids", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("kiro_subscription_models")
    op.drop_table("kiro_accounts")

