"""add pull request review runs

Revision ID: c3dd8f44db67
Revises: 4a64bc769bd1
Create Date: 2026-07-28 22:35:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3dd8f44db67"
down_revision: str | Sequence[str] | None = "4a64bc769bd1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "workflow_type",
            sa.String(length=40),
            server_default="specification",
            nullable=False,
        ),
    )
    op.add_column(
        "runs",
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("active_review_revision", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("runs") as batch:
        batch.alter_column(
            "requirement_type",
            existing_type=sa.String(length=20),
            nullable=True,
        )


def downgrade() -> None:
    op.execute("DELETE FROM runs WHERE workflow_type = 'pull_request_review'")
    with op.batch_alter_table("runs") as batch:
        batch.alter_column(
            "requirement_type",
            existing_type=sa.String(length=20),
            nullable=False,
        )
    op.drop_column("runs", "active_review_revision")
    op.drop_column("runs", "pull_request_number")
    op.drop_column("runs", "workflow_type")
