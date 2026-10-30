"""Purchases, sales and operating expenses."""
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from decimal import Decimal
from typing import Optional
from app.database import Base, TimestampMixin, utcnow

class StockReceipt(Base, TimestampMixin):
    __tablename__ = "stock_receipts"
    __table_args__ = (CheckConstraint("quantity > 0"), CheckConstraint("unit_cost >= 0"), Index("ix_receipts_date", "occurred_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    supplier: Mapped[Optional[str]] = mapped_column(String(250))
    reference_number: Mapped[Optional[str]] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    recorded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))

class MedicineSale(Base, TimestampMixin):
    __tablename__ = "medicine_sales"
    __table_args__ = (CheckConstraint("quantity > 0"), CheckConstraint("unit_price >= 0"), Index("ix_sales_date", "occurred_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    recorded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))

class OperatingExpense(Base, TimestampMixin):
    __tablename__ = "operating_expenses"
    __table_args__ = (CheckConstraint("amount >= 0"), Index("ix_expenses_date", "occurred_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    recorded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
