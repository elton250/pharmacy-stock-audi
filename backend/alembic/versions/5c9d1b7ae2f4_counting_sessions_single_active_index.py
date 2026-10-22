"""counting sessions: single active session partial unique index

Revision ID: 5c9d1b7ae2f4
Revises: 312abc5d983f
Create Date: 2026-10-21 10:45:03.117204

Enforces the business rule "only ONE active Static Counting session may exist at a
time across the system" at the database level, so concurrent requests / double
clicks / multiple browser tabs can never create two active sessions.

Active statuses: DRAFT, IN_PROGRESS, PENDING_REVIEW.
Archived rows are excluded so an archived draft cannot keep blocking new work.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5c9d1b7ae2f4'
down_revision: str | None = '312abc5d983f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_PREDICATE = sa.text(
    "status IN ('DRAFT', 'IN_PROGRESS', 'PENDING_REVIEW') AND archived_at IS NULL"
)


def upgrade() -> None:
    op.create_index(
        "uq_counting_sessions_single_active",
        "counting_sessions",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=ACTIVE_PREDICATE,
    )


def downgrade() -> None:
    op.drop_index("uq_counting_sessions_single_active", table_name="counting_sessions")
