"""Counting sessions, count events and their lines (strict static counting)."""
import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin, utcnow


class SessionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_REVIEW = "PENDING_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ReviewDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CountingSession(Base, TimestampMixin):
    __tablename__ = "counting_sessions"
    __table_args__ = (
        Index("ix_counting_sessions_created_by_status", "created_by", "status"),
        Index(
            "uq_counting_sessions_single_active",
            text("(1)"),
            unique=True,
            postgresql_where=text(
                "status IN ('DRAFT', 'IN_PROGRESS', 'PENDING_REVIEW') AND archived_at IS NULL"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=SessionStatus.DRAFT.value, index=True)
    active_medicine_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("medicines.id"), nullable=True)
    stock_audit_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("stock_audits.id"), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    review_decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    archive_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    active_medicine = relationship("Medicine", lazy="selectin")
    creator = relationship("User", foreign_keys=[created_by], lazy="selectin")
    reviewer = relationship("User", foreign_keys=[reviewed_by], lazy="selectin")
    events: Mapped[list["CountEvent"]] = relationship(
        "CountEvent", back_populates="session", cascade="all, delete-orphan", lazy="selectin"
    )


class CountEvent(Base):
    """One submitted count for the session's active medicine, with idempotency protection."""

    __tablename__ = "count_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_count_event_idempotency_key"),
        Index("ix_count_events_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("counting_sessions.id", ondelete="CASCADE"), nullable=False
    )
    medicine_id: Mapped[int] = mapped_column(Integer, ForeignKey("medicines.id"), nullable=False)
    operator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    total_base_units: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    session: Mapped[CountingSession] = relationship("CountingSession", back_populates="events")
    lines: Mapped[list["CountEventLine"]] = relationship(
        "CountEventLine", back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )


class CountEventLine(Base):
    __tablename__ = "count_event_lines"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_line_quantity_non_negative"),
        CheckConstraint("partial_units IS NULL OR partial_units >= 0", name="ck_line_partial_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    count_event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("count_events.id", ondelete="CASCADE"), nullable=False
    )
    package_config_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("package_configurations.id"), nullable=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    partial_units: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    base_units: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    is_partial_package: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    event: Mapped[CountEvent] = relationship("CountEvent", back_populates="lines")
    package = relationship("PackageConfiguration", lazy="selectin")
