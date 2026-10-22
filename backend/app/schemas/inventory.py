"""Inventory transaction schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.models.inventory import TransactionType


class InventoryTransactionCreate(BaseModel):
    medicine_id: int
    transaction_type: TransactionType
    quantity: Decimal = Field(gt=0, le=1_000_000_000)
    occurred_at: Optional[datetime] = None
    reference_number: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=500)


class InventoryTransactionOut(BaseModel):
    id: int
    medicine_id: int
    medicine_name: Optional[str] = None
    transaction_type: str
    signed_quantity: Decimal
    quantity: Decimal
    occurred_at: datetime
    recorded_by: Optional[int] = None
    recorded_by_username: Optional[str] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None

    model_config = {"from_attributes": True}
