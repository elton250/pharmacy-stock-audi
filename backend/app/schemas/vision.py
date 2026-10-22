"""Schemas for vision-assisted identification."""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class VisionCandidate(BaseModel):
    """A medicine the OCR text might refer to. A proposal, never a decision."""

    medicine_id: int
    name: str
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    strength: Optional[str] = None
    barcode: Optional[str] = None
    match_type: Literal["barcode_exact", "barcode_partial", "name"]
    score: float


class VisionCaptureOut(BaseModel):
    """State of one capture, including the proposals for the human to confirm."""

    capture_id: str
    status: str
    ocr_text: str = ""
    ocr_confidence: Optional[float] = None
    engine: Optional[str] = None
    engine_available: bool = True
    candidates: list[VisionCandidate] = Field(default_factory=list)
    matched_medicine_id: Optional[int] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    image_deleted: bool = False


class VisionTextScanIn(BaseModel):
    """Manually typed text (or a barcode) to match against the catalogue."""

    text: str = Field(min_length=1, max_length=500)


class VisionConfirmIn(BaseModel):
    """The human decision on a capture.

    ``decision='confirm'`` requires ``medicine_id``; ``decision='reject'``
    closes the capture without a match. Neither decision writes stock.
    """

    decision: Literal["confirm", "reject"]
    medicine_id: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=500)


class VisionStatusOut(BaseModel):
    """Availability of the local OCR engine (no secrets, no paths)."""

    enabled: bool
    engine: str
    engine_available: bool
    message: Optional[str] = None
    max_upload_bytes: int
    retention_days: int
