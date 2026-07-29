"""add operation progress

Revision ID: 4a64bc769bd1
Revises: 8c0216fd42af
Create Date: 2026-07-28 14:38:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4a64bc769bd1"
down_revision: str | Sequence[str] | None = "8c0216fd42af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operations",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )
    op.add_column(
        "operations",
        sa.Column(
            "progress_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("operations", "progress_at")
    op.drop_column("operations", "started_at")
