"""add_plugin_db_migration_states_table

Revision ID: ccac3ee092ba
Revises: 0dcd8c4a8684, 8b0d7c1f3e2a
Create Date: 2026-02-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ccac3ee092ba"
down_revision: Union[str, Sequence[str], None] = ("0dcd8c4a8684", "8b0d7c1f3e2a")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plugin_db_migration_states",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        sa.CheckConstraint(
            "status IN ('pending','running','done','failed')",
            name="ck_plugin_db_migration_states_status",
        ),
    )
    op.create_index(
        "ix_plugin_db_migration_states_status",
        "plugin_db_migration_states",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_db_migration_states_status", table_name="plugin_db_migration_states")
    op.drop_table("plugin_db_migration_states")

