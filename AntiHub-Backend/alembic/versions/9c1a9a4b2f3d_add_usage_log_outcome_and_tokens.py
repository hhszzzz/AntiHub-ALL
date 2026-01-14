"""add_usage_log_outcome_and_tokens

Revision ID: 9c1a9a4b2f3d
Revises: add_config_type
Create Date: 2026-01-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c1a9a4b2f3d"
down_revision: Union[str, None] = "add_config_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usage_logs", sa.Column("config_type", sa.String(length=20), nullable=True))
    op.add_column(
        "usage_logs",
        sa.Column("stream", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.add_column(
        "usage_logs",
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "usage_logs",
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "usage_logs",
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
    )

    op.add_column(
        "usage_logs",
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("usage_logs", sa.Column("status_code", sa.Integer(), nullable=True))
    op.add_column("usage_logs", sa.Column("error_message", sa.Text(), nullable=True))

    op.add_column(
        "usage_logs",
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_index(op.f("ix_usage_logs_config_type"), "usage_logs", ["config_type"], unique=False)
    op.create_index(op.f("ix_usage_logs_success"), "usage_logs", ["success"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_logs_success"), table_name="usage_logs")
    op.drop_index(op.f("ix_usage_logs_config_type"), table_name="usage_logs")

    op.drop_column("usage_logs", "duration_ms")
    op.drop_column("usage_logs", "error_message")
    op.drop_column("usage_logs", "status_code")
    op.drop_column("usage_logs", "success")
    op.drop_column("usage_logs", "total_tokens")
    op.drop_column("usage_logs", "output_tokens")
    op.drop_column("usage_logs", "input_tokens")
    op.drop_column("usage_logs", "stream")
    op.drop_column("usage_logs", "config_type")

