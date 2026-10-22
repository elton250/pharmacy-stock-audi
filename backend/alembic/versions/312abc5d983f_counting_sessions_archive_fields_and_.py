"""counting sessions: archive fields and indexes

Revision ID: 312abc5d983f
Revises: 0dcb2a75fe7b
Create Date: 2026-10-21 10:20:16.583455

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '312abc5d983f'
down_revision: str | None = '0dcb2a75fe7b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('counting_sessions', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('counting_sessions', sa.Column('archived_by', sa.BigInteger(), nullable=True))
    op.add_column('counting_sessions', sa.Column('archive_reason', sa.String(length=500), nullable=True))
    op.create_foreign_key('fk_counting_sessions_archived_by_users', 'counting_sessions', 'users', ['archived_by'], ['id'])
    op.create_index('ix_counting_sessions_created_by_status', 'counting_sessions', ['created_by', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_counting_sessions_created_by_status', table_name='counting_sessions')
    op.drop_constraint('fk_counting_sessions_archived_by_users', 'counting_sessions', type_='foreignkey')
    op.drop_column('counting_sessions', 'archive_reason')
    op.drop_column('counting_sessions', 'archived_by')
    op.drop_column('counting_sessions', 'archived_at')
