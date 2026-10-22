"""vision captures: table for vision-assisted identification

Revision ID: c8f2a4b91d7e
Revises: b71f4a92c308
Create Date: 2026-10-22 14:05:00.000000

Phase 2A stores the *proposals* of the camera/OCR flow: the recognised text
and the candidate medicines derived from it. A capture never represents a
confirmed stock movement.

Safe, additive change:
  * a new table only — no existing table, column, index or constraint is
    touched, so all existing data (users, medicines, counts, inventory,
    audits) is preserved exactly as it was;
  * the table is referenced by the new code only, so the previous application
    version keeps working unchanged after the migration;
  * the foreign keys are created without ON DELETE CASCADE on users, so a
    capture is never removed as a side effect of a user deletion.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f2a4b91d7e"
down_revision: str | None = "b71f4a92c308"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vision_captures",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("image_sha256", sa.String(length=64), nullable=True),
        sa.Column("content_type", sa.String(length=50), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ocr_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("ocr_confidence", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("engine", sa.String(length=50), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("matched_medicine_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["matched_medicine_id"], ["medicines.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vision_captures_user_id", "vision_captures", ["user_id"])
    op.create_index("ix_vision_captures_status", "vision_captures", ["status"])
    op.create_index("ix_vision_captures_image_sha256", "vision_captures", ["image_sha256"])
    op.create_index("ix_vision_captures_expires_at", "vision_captures", ["expires_at"])
    op.create_index(
        "ix_vision_captures_status_created", "vision_captures", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_vision_captures_status_created", table_name="vision_captures")
    op.drop_index("ix_vision_captures_expires_at", table_name="vision_captures")
    op.drop_index("ix_vision_captures_image_sha256", table_name="vision_captures")
    op.drop_index("ix_vision_captures_status", table_name="vision_captures")
    op.drop_index("ix_vision_captures_user_id", table_name="vision_captures")
    op.drop_table("vision_captures")
