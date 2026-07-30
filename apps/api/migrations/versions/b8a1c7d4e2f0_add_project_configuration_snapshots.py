"""add project configuration snapshots

Revision ID: b8a1c7d4e2f0
Revises: a6f27d4e91bc
Create Date: 2026-07-30 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8a1c7d4e2f0"
down_revision: str | Sequence[str] | None = "a6f27d4e91bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("project_configuration", sa.JSON(), nullable=True))
    op.add_column("phases", sa.Column("project_configuration", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("phases", "project_configuration")
    op.drop_column("runs", "project_configuration")
