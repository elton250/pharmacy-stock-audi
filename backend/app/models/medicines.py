"""Medicine master data: medicines, barcodes and package configurations."""
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Medicine(Base, TimestampMixin):
    __tablename__ = "medicines"
    __table_args__ = (Index("ix_medicines_name_generic", "name", "generic_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    generic_name: Mapped[Optional[str]] = mapped_column(String(250), nullable=True, index=True)
    brand_name: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)
    strength: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    dosage_form: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    base_unit: Mapped[str] = mapped_column(String(50), nullable=False, default="tablet")
    opening_stock: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    barcodes: Mapped[list["MedicineBarcode"]] = relationship(
        "MedicineBarcode", back_populates="medicine", cascade="all, delete-orphan", lazy="selectin"
    )
    packages: Mapped[list["PackageConfiguration"]] = relationship(
        "PackageConfiguration",
        back_populates="medicine",
        cascade="all, delete-orphan",
        order_by="PackageConfiguration.sort_order",
        lazy="selectin",
    )


class MedicineBarcode(Base, TimestampMixin):
    __tablename__ = "medicine_barcodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    barcode: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    medicine: Mapped[Medicine] = relationship("Medicine", back_populates="barcodes")


class PackageConfiguration(Base, TimestampMixin):
    """A package level for a specific medicine.

    ``parent_id`` is NULL when the package directly contains base units.
    ``units_per_parent`` is how many units of the parent (or base unit when
    parent is NULL) make up one of this package.
    """

    __tablename__ = "package_configurations"
    __table_args__ = (UniqueConstraint("medicine_id", "name", name="uq_package_medicine_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("package_configurations.id", ondelete="CASCADE"), nullable=True
    )
    units_per_parent: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    medicine: Mapped[Medicine] = relationship("Medicine", back_populates="packages")
    parent: Mapped[Optional["PackageConfiguration"]] = relationship(
        "PackageConfiguration", remote_side=[id], lazy="selectin"
    )
