"""add stock receipts, medicine sales and operating expenses
Revision ID: d4a8b71c2e90
Revises: c8f2a4b91d7e
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
revision: str = "d4a8b71c2e90"
down_revision: str | None = "c8f2a4b91d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table("stock_receipts",
        sa.Column("id",sa.BigInteger(),autoincrement=True,nullable=False),
        sa.Column("medicine_id",sa.Integer(),nullable=False),
        sa.Column("quantity",sa.Numeric(14,3),nullable=False),
        sa.Column("unit_cost",sa.Numeric(14,2),nullable=False),
        sa.Column("supplier",sa.String(250)),
        sa.Column("reference_number",sa.String(120)),
        sa.Column("occurred_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("recorded_by",sa.BigInteger()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("quantity > 0"),sa.CheckConstraint("unit_cost >= 0"),
        sa.ForeignKeyConstraint(["medicine_id"],["medicines.id"]),
        sa.ForeignKeyConstraint(["recorded_by"],["users.id"]),sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_receipts_date","stock_receipts",["occurred_at"])
    op.create_table("medicine_sales",
        sa.Column("id",sa.BigInteger(),autoincrement=True,nullable=False),
        sa.Column("medicine_id",sa.Integer(),nullable=False),
        sa.Column("quantity",sa.Numeric(14,3),nullable=False),
        sa.Column("unit_price",sa.Numeric(14,2),nullable=False),
        sa.Column("occurred_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("recorded_by",sa.BigInteger()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("quantity > 0"),sa.CheckConstraint("unit_price >= 0"),
        sa.ForeignKeyConstraint(["medicine_id"],["medicines.id"]),
        sa.ForeignKeyConstraint(["recorded_by"],["users.id"]),sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_sales_date","medicine_sales",["occurred_at"])
    op.create_table("operating_expenses",
        sa.Column("id",sa.BigInteger(),autoincrement=True,nullable=False),
        sa.Column("category",sa.String(100),nullable=False),
        sa.Column("description",sa.String(500),nullable=False),
        sa.Column("amount",sa.Numeric(14,2),nullable=False),
        sa.Column("occurred_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("recorded_by",sa.BigInteger()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("amount >= 0"),
        sa.ForeignKeyConstraint(["recorded_by"],["users.id"]),sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_expenses_date","operating_expenses",["occurred_at"])

def downgrade() -> None:
    op.drop_index("ix_expenses_date",table_name="operating_expenses")
    op.drop_table("operating_expenses")
    op.drop_index("ix_sales_date",table_name="medicine_sales")
    op.drop_table("medicine_sales")
    op.drop_index("ix_receipts_date",table_name="stock_receipts")
    op.drop_table("stock_receipts")
