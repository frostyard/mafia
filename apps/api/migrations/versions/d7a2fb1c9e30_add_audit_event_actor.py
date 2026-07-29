"""add audit event actor

Revision ID: d7a2fb1c9e30
Revises: c3dd8f44db67
Create Date: 2026-07-29 01:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a2fb1c9e30"
down_revision: str | Sequence[str] | None = "c3dd8f44db67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.add_column(
            sa.Column(
                "actor",
                sa.String(length=100),
                nullable=False,
                server_default="system",
            )
        )
    with op.batch_alter_table("decisions") as batch:
        batch.alter_column(
            "actor",
            existing_type=sa.String(length=50),
            type_=sa.String(length=100),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("decisions") as batch:
        batch.alter_column(
            "actor",
            existing_type=sa.String(length=100),
            type_=sa.String(length=50),
            existing_nullable=False,
        )
    with op.batch_alter_table("events") as batch:
        batch.drop_column("actor")
