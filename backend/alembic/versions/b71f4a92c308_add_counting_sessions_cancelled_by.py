"""counting sessions: cancelled_by column

Revision ID: b71f4a92c308
Revises: 5c9d1b7ae2f4
Create Date: 2026-10-21 12:20:00.000000

Records WHO cancelled a counting session. The audit log already stores the
acting user, but the session row itself now carries the actor so the
cancellation is visible from the session record (and from the UI) without
having to join the audit log.

Safe, additive change:
  * the new column is NULLABLE, so every existing row (including sessions that
    were cancelled before this migration) keeps its data untouched;
  * no existing column is dropped, renamed or retyped;
  * no data is rewritten, so sessions, counts, inventory and audit history are
    preserved exactly as they were.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b71f4a92c308"
down_revision: str | None = "5c9d1b7ae2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "counting_sessions",
        sa.Column("cancelled_by", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_counting_sessions_cancelled_by_users",
        "counting_sessions",
        "users",
        ["cancelled_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_counting_sessions_cancelled_by_users", "counting_sessions", type_="foreignkey"
    )
    op.drop_column("counting_sessions", "cancelled_by")
