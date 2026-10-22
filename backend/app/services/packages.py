"""Medicine-specific package conversion to base units."""
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.errors import ApiError
from app.models.medicines import PackageConfiguration


def package_units_map(db: DbSession, medicine_id: int) -> dict[int, PackageConfiguration]:
    rows = db.execute(
        select(PackageConfiguration).where(PackageConfiguration.medicine_id == medicine_id)
    ).scalars().all()
    return {p.id: p for p in rows}


def units_in_base(pkg: PackageConfiguration, by_id: dict[int, PackageConfiguration]) -> Decimal:
    """How many base units one full package equals, walking up the parent chain."""
    max_depth = get_settings().max_package_chain_depth
    total = Decimal("1")
    current: Optional[PackageConfiguration] = pkg
    seen: set[int] = set()
    depth = 0
    while current is not None:
        if current.id in seen:
            raise ApiError(422, "Package configuration contains a cycle", "package_cycle")
        seen.add(current.id)
        total *= Decimal(current.units_per_parent)
        current = by_id.get(current.parent_id) if current.parent_id else None
        depth += 1
        if depth > max_depth:
            raise ApiError(422, f"Package chain deeper than {max_depth} levels", "package_depth_exceeded")
    return total


def base_units_for_line(
    package: Optional[PackageConfiguration],
    quantity: Decimal,
    partial_units: Optional[Decimal],
    by_id: dict[int, PackageConfiguration],
) -> tuple[Decimal, bool]:
    """Convert one count line into base units.

    - Base-unit line: package is None → base = quantity.
    - Full package line: base = quantity * units_in_base(package).
    - Cut/partial package line: partial_units are expressed in the package's
      immediate child (base unit when the package has no parent); the child
      level is converted to base units.
    """
    if package is None:
        if partial_units is not None:
            raise ApiError(422, "partial_units is only valid with a package", "invalid_line")
        return quantity, False

    if partial_units is not None:
        parent = by_id.get(package.parent_id) if package.parent_id else None
        if parent is not None:
            child_base = units_in_base(parent, by_id)
        else:
            child_base = Decimal("1")
        return (partial_units * child_base).quantize(Decimal("0.001")), True

    return (quantity * units_in_base(package, by_id)).quantize(Decimal("0.001")), False


def validate_package_parent(
    db: DbSession, medicine_id: int, package_id: Optional[int], parent_id: Optional[int]
) -> None:
    """Reject self-parenting and cycles when configuring packages."""
    if parent_id is None:
        return
    if package_id is not None and package_id == parent_id:
        raise ApiError(422, "A package cannot be its own parent", "package_cycle")
    by_id = package_units_map(db, medicine_id)
    parent = by_id.get(parent_id)
    if parent is None:
        raise ApiError(422, "Parent package does not belong to this medicine", "invalid_parent")
    # Walk up from the proposed parent to ensure the new link cannot create a cycle.
    current: Optional[PackageConfiguration] = parent
    seen: set[int] = set()
    while current is not None:
        if current.id == package_id:
            raise ApiError(422, "Package parent would create a cycle", "package_cycle")
        if current.id in seen:
            break
        seen.add(current.id)
        current = by_id.get(current.parent_id) if current.parent_id else None
