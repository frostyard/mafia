"""add operation observability

Revision ID: 8c0216fd42af
Revises: fe95154b9ff4
Create Date: 2026-07-28 14:18:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c0216fd42af"
down_revision: str | Sequence[str] | None = "fe95154b9ff4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("model", sa.String(length=100), nullable=True))
    op.add_column(
        "operations",
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column("operations", sa.Column("timeout_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "operations",
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )
    op.add_column(
        "operations",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "operations",
        sa.Column("detail", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.create_index("ix_operations_run_status", "operations", ["run_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_operations_run_status", table_name="operations")
    op.drop_column("operations", "detail")
    op.drop_column("operations", "completed_at")
    op.drop_column("operations", "heartbeat_at")
    op.drop_column("operations", "timeout_seconds")
    op.drop_column("operations", "attempt")
    op.drop_column("operations", "model")
