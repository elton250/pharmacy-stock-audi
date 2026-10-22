"""Stock audit and variance report schemas."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class StockAuditCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    audit_date: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    medicine_ids: list[int] = []


class AuditItemAdd(BaseModel):
    medicine_ids: list[int] = Field(min_length=1)


class StockAuditItemOut(BaseModel):
    id: int
    medicine_id: int
    medicine_name: Optional[str] = None
    medicine_generic: Optional[str] = None
    expected_stock: Decimal
    physical_stock: Optional[Decimal] = None
    variance: Optional[Decimal] = None
    classification: Optional[str] = None
    notes: Optional[str] = None
    counted_by: Optional[int] = None
    counter_username: Optional[str] = None
    reviewed_by: Optional[int] = None
    reviewer_username: Optional[str] = None
    counted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class StockAuditOut(BaseModel):
    id: int
    code: str
    title: str
    status: str
    audit_date: date
    notes: Optional[str] = None
    initiated_by: int
    initiator_username: Optional[str] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    items: list[StockAuditItemOut] = []

    model_config = {"from_attributes": True}


class VarianceReportRow(BaseModel):
    medicine_id: int
    medicine_name: str
    generic_name: Optional[str] = None
    base_unit: str
    opening_stock: Decimal
    received: Decimal
    issued: Decimal
    returns_in: Decimal
    returns_out: Decimal
    adjustments_positive: Decimal
    adjustments_negative: Decimal
    expected_stock: Decimal
    physical_stock: Optional[Decimal] = None
    variance: Optional[Decimal] = None
    classification: str = "UNCOUNTED"
    counting_operator: Optional[str] = None
    reviewer: Optional[str] = None
    audit_date: Optional[date] = None
    counting_session_code: Optional[str] = None
    notes: Optional[str] = None


class StockLevelRow(BaseModel):
    medicine_id: int
    medicine_name: str
    base_unit: str
    expected_stock: Decimal
    reorder_level: Decimal
    below_reorder: bool
    is_active: bool
