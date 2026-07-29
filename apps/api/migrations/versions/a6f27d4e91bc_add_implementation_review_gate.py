"""add implementation review gate

Revision ID: a6f27d4e91bc
Revises: d7a2fb1c9e30
Create Date: 2026-07-29 11:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6f27d4e91bc"
down_revision: str | Sequence[str] | None = "d7a2fb1c9e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "phases",
        sa.Column("review_cycle", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "phases",
        sa.Column(
            "implementation_review_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "phases",
        sa.Column("remediation_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "phases",
        sa.Column("verification_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("phases", sa.Column("candidate_base_sha", sa.String(length=64), nullable=True))
    op.add_column("phases", sa.Column("candidate_diff_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("phases", "candidate_diff_hash")
    op.drop_column("phases", "candidate_base_sha")
    op.drop_column("phases", "verification_attempts")
    op.drop_column("phases", "remediation_attempts")
    op.drop_column("phases", "implementation_review_attempts")
    op.drop_column("phases", "review_cycle")
