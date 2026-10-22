"""add users.must_change_password

Revision ID: 0dcb2a75fe7b
Revises: de59fc2743eb
Create Date: 2026-10-18 22:44:16.806396

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0dcb2a75fe7b'
down_revision: str | None = 'de59fc2743eb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
