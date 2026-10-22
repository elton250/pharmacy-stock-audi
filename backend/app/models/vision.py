"""Vision-assisted identification captures.

A capture is a *proposal*: the OCR text and the candidate medicines derived
from it. It never represents a confirmed stock movement — a human confirms
the identification separately, and only then is the capture closed.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin


class CaptureStatus:
    PENDING = "pending"        # uploaded, awaiting a human decision
    CONFIRMED = "confirmed"    # a human confirmed one candidate
    REJECTED = "rejected"      # a human rejected all candidates
    EXPIRED = "expired"        # retention window elapsed, image deleted

    ALL = (PENDING, CONFIRMED, REJECTED, EXPIRED)


class VisionCapture(Base, TimestampMixin):
    __tablename__ = "vision_captures"
    __table_args__ = (
        Index("ix_vision_captures_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CaptureStatus.PENDING, index=True)

    # The image file lives in a non-public runtime directory; the path is
    # cleared once the image has been deleted.
    image_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    image_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ocr_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    engine: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    matched_medicine_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
