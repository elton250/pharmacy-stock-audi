"""Inventory transactions."""
import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, utcnow


class TransactionType(str, enum.Enum):
    OPENING = "OPENING"
    RECEIVED = "RECEIVED"
    ISSUED = "ISSUED"
    RETURN_IN = "RETURN_IN"
    RETURN_OUT = "RETURN_OUT"
    ADJUSTMENT_POSITIVE = "ADJUSTMENT_POSITIVE"
    ADJUSTMENT_NEGATIVE = "ADJUSTMENT_NEGATIVE"


# Multiplier applied to the (positive) quantity when computing expected stock.
SIGN: dict[TransactionType, int] = {
    TransactionType.OPENING: 1,
    TransactionType.RECEIVED: 1,
    TransactionType.RETURN_IN: 1,
    TransactionType.ADJUSTMENT_POSITIVE: 1,
    TransactionType.ISSUED: -1,
    TransactionType.RETURN_OUT: -1,
    TransactionType.ADJUSTMENT_NEGATIVE: -1,
}


class InventoryTransaction(Base, TimestampMixin):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_transaction_quantity_positive"),
        Index("ix_transactions_medicine_occurred", "medicine_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(Integer, ForeignKey("medicines.id"), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    recorded_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    reference_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    counting_session_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("counting_sessions.id"), nullable=True
    )
    stock_audit_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("stock_audits.id"), nullable=True)

    @property
    def type_enum(self) -> TransactionType:
        return TransactionType(self.transaction_type)

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity * SIGN[TransactionType(self.transaction_type)]
