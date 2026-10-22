"""Counting session and count event schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.models.counting import ReviewDecision


class CountingSessionCreate(BaseModel):
    medicine_id: Optional[int] = None  # activate immediately when provided
    stock_audit_id: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class ActivateRequest(BaseModel):
    medicine_id: int


class CountEventLineIn(BaseModel):
    package_config_id: Optional[int] = None  # None => base units
    quantity: Decimal = Field(default=Decimal("0"), ge=0)
    partial_units: Optional[Decimal] = Field(default=None, ge=0)  # cut/partial package


class CountSubmitRequest(BaseModel):
    medicine_id: Optional[int] = None  # must match active medicine when provided
    idempotency_key: str = Field(min_length=8, max_length=100)
    lines: list[CountEventLineIn] = Field(min_length=1)
    notes: Optional[str] = Field(default=None, max_length=500)


class CountEventLineOut(BaseModel):
    id: int
    package_config_id: Optional[int] = None
    package_name: Optional[str] = None
    quantity: Decimal
    partial_units: Optional[Decimal] = None
    base_units: Decimal
    is_partial_package: bool

    model_config = {"from_attributes": True}


class CountEventOut(BaseModel):
    id: int
    session_id: int
    medicine_id: int
    operator_id: int
    operator_username: Optional[str] = None
    total_base_units: Decimal
    idempotency_key: str
    notes: Optional[str] = None
    created_at: datetime
    lines: list[CountEventLineOut] = []

    model_config = {"from_attributes": True}


class CountingSessionOut(BaseModel):
    id: int
    code: str
    status: str
    active_medicine_id: Optional[int] = None
    active_medicine_name: Optional[str] = None
    active_medicine_barcode: Optional[str] = None
    stock_audit_id: Optional[int] = None
    created_by: int
    creator_username: Optional[str] = None
    submitted_at: Optional[datetime] = None
    reviewed_by: Optional[int] = None
    reviewer_username: Optional[str] = None
    review_decision: Optional[str] = None
    review_notes: Optional[str] = None
    review_count: int = 0
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[int] = None
    cancelled_by_username: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    total_counted_base_units: Optional[Decimal] = None
    archived_at: Optional[datetime] = None
    archived_by: Optional[int] = None
    archived_by_username: Optional[str] = None
    archive_reason: Optional[str] = None
    events: list[CountEventOut] = []

    model_config = {"from_attributes": True}


class CancelSessionRequest(BaseModel):
    """Optional reason recorded in the audit trail when a session is cancelled."""

    reason: Optional[str] = Field(default=None, max_length=500)


class ArchiveSessionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    notes: Optional[str] = Field(default=None, max_length=500)
