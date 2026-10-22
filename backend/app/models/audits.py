"""Physical stock audit sessions and their medicine items (variance tracking)."""
import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class StockAuditStatus(str, enum.Enum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class VarianceClassification(str, enum.Enum):
    MATCH = "MATCH"
    SHORTAGE = "SHORTAGE"
    SURPLUS = "SURPLUS"
    UNCOUNTED = "UNCOUNTED"


class StockAudit(Base, TimestampMixin):
    __tablename__ = "stock_audits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=StockAuditStatus.OPEN.value, index=True)
    audit_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    initiated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    completed_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    initiator = relationship("User", foreign_keys=[initiated_by], lazy="selectin")
    items: Mapped[list["StockAuditItem"]] = relationship(
        "StockAuditItem", back_populates="audit", cascade="all, delete-orphan", lazy="selectin"
    )


class StockAuditItem(Base, TimestampMixin):
    __tablename__ = "stock_audit_items"
    __table_args__ = (UniqueConstraint("stock_audit_id", "medicine_id", name="uq_audit_item_medicine"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_audit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("stock_audits.id", ondelete="CASCADE"), nullable=False
    )
    medicine_id: Mapped[int] = mapped_column(Integer, ForeignKey("medicines.id"), nullable=False)
    expected_stock: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    physical_stock: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    variance: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    classification: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    counted_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    counted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    audit: Mapped[StockAudit] = relationship("StockAudit", back_populates="items")
    medicine = relationship("Medicine", lazy="selectin")
    counter = relationship("User", foreign_keys=[counted_by], lazy="selectin")
    reviewer = relationship("User", foreign_keys=[reviewed_by], lazy="selectin")
