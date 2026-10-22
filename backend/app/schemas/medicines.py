"""Medicine, barcode and package schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class BarcodeOut(BaseModel):
    id: int
    medicine_id: int
    barcode: str
    is_primary: bool
    note: Optional[str] = None

    model_config = {"from_attributes": True}


class BarcodeCreate(BaseModel):
    barcode: str = Field(min_length=3, max_length=100)
    is_primary: bool = False
    note: Optional[str] = Field(default=None, max_length=300)


class PackageOut(BaseModel):
    id: int
    medicine_id: int
    name: str
    parent_id: Optional[int] = None
    units_per_parent: Decimal
    sort_order: int
    units_in_base: Optional[Decimal] = None

    model_config = {"from_attributes": True}


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: Optional[int] = None
    parent_name: Optional[str] = Field(default=None, max_length=80)
    units_per_parent: Decimal = Field(gt=0, le=1_000_000)
    sort_order: int = 0


class PackageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    parent_id: Optional[int] = None
    units_per_parent: Optional[Decimal] = Field(default=None, gt=0, le=1_000_000)
    sort_order: Optional[int] = None


class MedicineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    generic_name: Optional[str] = Field(default=None, max_length=250)
    brand_name: Optional[str] = Field(default=None, max_length=250)
    strength: Optional[str] = Field(default=None, max_length=100)
    dosage_form: Optional[str] = Field(default=None, max_length=100)
    manufacturer: Optional[str] = Field(default=None, max_length=250)
    category: Optional[str] = Field(default=None, max_length=120)
    base_unit: str = Field(default="tablet", min_length=1, max_length=50)
    opening_stock: Decimal = Field(default=Decimal("0"), ge=0)
    reorder_level: Decimal = Field(default=Decimal("0"), ge=0)
    barcodes: list[BarcodeCreate] = []
    packages: list[PackageCreate] = []


class MedicineUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=250)
    generic_name: Optional[str] = Field(default=None, max_length=250)
    brand_name: Optional[str] = Field(default=None, max_length=250)
    strength: Optional[str] = Field(default=None, max_length=100)
    dosage_form: Optional[str] = Field(default=None, max_length=100)
    manufacturer: Optional[str] = Field(default=None, max_length=250)
    category: Optional[str] = Field(default=None, max_length=120)
    base_unit: Optional[str] = Field(default=None, min_length=1, max_length=50)
    opening_stock: Optional[Decimal] = Field(default=None, ge=0)
    reorder_level: Optional[Decimal] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class MedicineOut(BaseModel):
    id: int
    name: str
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    strength: Optional[str] = None
    dosage_form: Optional[str] = None
    manufacturer: Optional[str] = None
    category: Optional[str] = None
    base_unit: str
    opening_stock: Decimal
    reorder_level: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime
    barcodes: list[BarcodeOut] = []
    packages: list[PackageOut] = []
    expected_stock: Optional[Decimal] = None

    model_config = {"from_attributes": True}


class ScanResult(BaseModel):
    found: bool
    message: Optional[str] = None
    medicine: Optional[MedicineOut] = None
    barcode: Optional[str] = None
