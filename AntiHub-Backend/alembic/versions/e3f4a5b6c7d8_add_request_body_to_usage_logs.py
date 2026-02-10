"""add_request_body_to_usage_logs

Revision ID: e3f4a5b6c7d8
Revises: d4e5f6a7b8c9
Create Date: 2026-02-10

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 添加 request_body 列到 usage_logs 表
    op.add_column(
        "usage_logs",
        sa.Column("request_body", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("usage_logs", "request_body")
