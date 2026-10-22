"""Medicine master data, barcode-first identification and package configuration."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.database import get_db
from app.deps import require_permission
from app.errors import ApiError
from app.models.inventory import InventoryTransaction, TransactionType
from app.models.medicines import Medicine, MedicineBarcode, PackageConfiguration
from app.models.users import User
from app.schemas.common import Page
from app.schemas.medicines import (
    BarcodeCreate,
    BarcodeOut,
    MedicineCreate,
    MedicineOut,
    MedicineUpdate,
    PackageCreate,
    PackageOut,
    PackageUpdate,
    ScanResult,
)
from app.services.packages import base_units_for_line, package_units_map, units_in_base, validate_package_parent
from app.services.stock import expected_stock

router = APIRouter(prefix="/api/medicines", tags=["medicines"])


def _medicine_snapshot(m: Medicine) -> dict:
    return {
        "name": m.name,
        "generic_name": m.generic_name,
        "brand_name": m.brand_name,
        "strength": m.strength,
        "dosage_form": m.dosage_form,
        "manufacturer": m.manufacturer,
        "category": m.category,
        "base_unit": m.base_unit,
        "opening_stock": str(m.opening_stock),
        "reorder_level": str(m.reorder_level),
        "is_active": m.is_active,
        "barcodes": [b.barcode for b in m.barcodes],
        "packages": [
            {"name": p.name, "parent_id": p.parent_id, "units_per_parent": str(p.units_per_parent)}
            for p in m.packages
        ],
    }


def _get_medicine_or_404(db: DbSession, medicine_id: int) -> Medicine:
    medicine = db.get(Medicine, medicine_id)
    if medicine is None:
        raise ApiError(404, "Medicine not found", "not_found")
    return medicine


BARCODE_UNIQUE_CONSTRAINT = "ix_medicine_barcodes_barcode"


def _is_barcode_unique_violation(exc: IntegrityError) -> bool:
    """True only when the failure is the global barcode uniqueness constraint.

    Any other integrity error (another constraint, another table) has to keep
    bubbling up untouched: turning unrelated database failures into
    "duplicate barcode" errors would hide real problems.
    """
    orig = exc.orig
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint is not None:
        return constraint == BARCODE_UNIQUE_CONSTRAINT
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate == "23505" and BARCODE_UNIQUE_CONSTRAINT in str(orig)


def _package_out(db: DbSession, pkg: PackageConfiguration) -> PackageOut:
    by_id = package_units_map(db, pkg.medicine_id)
    out = PackageOut.model_validate(pkg)
    out.units_in_base = units_in_base(pkg, by_id)
    return out


def _validate_barcodes(db: DbSession, medicine: Optional[Medicine], barcodes: list[BarcodeCreate]) -> None:
    """Reject barcodes that are already registered.

    A barcode is globally unique, so two cases have to be distinguished:

    * the barcode is registered to a *different* medicine -> 409 ``barcode_taken``
      (a barcode may not be stolen from or moved to another medicine);
    * the barcode is already registered to *this* medicine -> 409
      ``barcode_already_exists``. Re-registering it is a client error; without
      this branch the INSERT reached the unique index and failed with an
      unhandled IntegrityError, which surfaced as HTTP 500.
    """
    seen: set[str] = set()
    for b in barcodes:
        code = b.barcode.strip()
        if not code:
            raise ApiError(422, "Barcode values cannot be empty", "invalid_barcode")
        if code in seen:
            raise ApiError(422, f"Duplicate barcode in request: {code}", "duplicate_barcode")
        seen.add(code)
        existing = db.execute(select(MedicineBarcode).where(MedicineBarcode.barcode == code)).scalar_one_or_none()
        if existing is None:
            continue
        if medicine is None or existing.medicine_id != medicine.id:
            raise ApiError(409, f"Barcode {code} is already registered", "barcode_taken")
        raise ApiError(
            409,
            f"Barcode {code} is already registered for this medicine",
            "barcode_already_exists",
        )


def _raise_barcode_conflict(db: DbSession, medicine: Optional[Medicine], code: str) -> None:
    """Raise the precise 409 for a barcode that turned out to be taken.

    Used for the uniqueness race (another transaction registered the barcode
    between the check and the INSERT): the owning row decides which of the two
    conflict codes applies.
    """
    existing = db.execute(
        select(MedicineBarcode).where(MedicineBarcode.barcode == code)
    ).scalar_one_or_none()
    if existing is not None and medicine is not None and existing.medicine_id == medicine.id:
        raise ApiError(
            409,
            f"Barcode {code} is already registered for this medicine",
            "barcode_already_exists",
        )
    raise ApiError(409, f"Barcode {code} is already registered", "barcode_taken")


def _apply_barcodes(db: DbSession, medicine: Medicine, barcodes: list[BarcodeCreate], user: User) -> None:
    _validate_barcodes(db, medicine, barcodes)
    medicine.barcodes = [
        MedicineBarcode(
            barcode=b.barcode.strip(),
            is_primary=b.is_primary,
            note=b.note,
            created_by=user.id,
        )
        for b in barcodes
    ]
    primaries = [b for b in medicine.barcodes if b.is_primary]
    if len(primaries) > 1:
        raise ApiError(422, "Only one primary barcode is allowed", "multiple_primary_barcodes")


@router.get("", response_model=Page[MedicineOut])
def list_medicines(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("medicine:view")),
    search: Optional[str] = Query(default=None, max_length=250),
    category: Optional[str] = Query(default=None, max_length=120),
    is_active: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    query = select(Medicine)
    count_query = select(func.count()).select_from(Medicine)
    if search:
        pattern = f"%{search}%"
        cond = or_(
            Medicine.name.ilike(pattern),
            Medicine.generic_name.ilike(pattern),
            Medicine.brand_name.ilike(pattern),
            Medicine.manufacturer.ilike(pattern),
        )
        query = query.where(cond)
        count_query = count_query.where(cond)
    if category:
        query = query.where(Medicine.category == category)
        count_query = count_query.where(Medicine.category == category)
    if is_active is not None:
        query = query.where(Medicine.is_active == is_active)
        count_query = count_query.where(Medicine.is_active == is_active)
    total = db.execute(count_query).scalar_one()
    rows = (
        db.execute(query.order_by(Medicine.name).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    items = [MedicineOut.model_validate(m) for m in rows]
    for out, m in zip(items, rows):
        out.expected_stock = expected_stock(db, m.id)
    return Page[MedicineOut](items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=MedicineOut, status_code=201)
def create_medicine(
    payload: MedicineCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("medicine:manage")),
):
    medicine = Medicine(
        name=payload.name,
        generic_name=payload.generic_name,
        brand_name=payload.brand_name,
        strength=payload.strength,
        dosage_form=payload.dosage_form,
        manufacturer=payload.manufacturer,
        category=payload.category,
        base_unit=payload.base_unit,
        opening_stock=payload.opening_stock,
        reorder_level=payload.reorder_level,
        is_active=True,
        created_by=user.id,
    )
    db.add(medicine)
    db.flush()

    # Barcodes
    _validate_barcodes(db, medicine, payload.barcodes)
    if payload.barcodes:
        try:
            # SAVEPOINT: a uniqueness race (another transaction registered the
            # same barcode between the check above and this INSERT) must be
            # reported as a conflict and must not poison the outer transaction.
            with db.begin_nested():
                for b in payload.barcodes:
                    db.add(
                        MedicineBarcode(
                            medicine_id=medicine.id,
                            barcode=b.barcode.strip(),
                            is_primary=b.is_primary,
                            note=b.note,
                            created_by=user.id,
                        )
                    )
                db.flush()
        except IntegrityError as exc:
            if _is_barcode_unique_violation(exc):
                _raise_barcode_conflict(db, medicine, payload.barcodes[0].barcode.strip())
            raise

    # Packages: create without parents first, then link parents by name
    pkg_rows: dict[str, PackageConfiguration] = {}
    parent_refs: list[tuple[PackageConfiguration, str]] = []
    for p in payload.packages:
        if p.name in pkg_rows:
            raise ApiError(422, f"Duplicate package name: {p.name}", "duplicate_package")
        if p.parent_id is not None:
            raise ApiError(422, "parent_id is only supported when editing; use parent_name on create", "invalid_parent")
        row = PackageConfiguration(
            medicine_id=medicine.id,
            name=p.name,
            units_per_parent=p.units_per_parent,
            sort_order=p.sort_order,
            created_by=user.id,
        )
        pkg_rows[p.name] = row
        db.add(row)
        if p.parent_name:
            parent_refs.append((row, p.parent_name))
    db.flush()
    for row, parent_name in parent_refs:
        parent = pkg_rows.get(parent_name)
        if parent is None:
            raise ApiError(422, f"Unknown parent package name: {parent_name}", "invalid_parent")
        row.parent_id = parent.id
        db.add(row)
    if parent_refs:
        db.flush()

    # Opening stock transaction (single source of truth for expected stock)
    if payload.opening_stock and payload.opening_stock > 0:
        db.add(
            InventoryTransaction(
                medicine_id=medicine.id,
                transaction_type=TransactionType.OPENING.value,
                quantity=payload.opening_stock,
                recorded_by=user.id,
                notes="Opening stock at medicine registration",
            )
        )

    log_action(
        db,
        action="medicine.create",
        entity_type="medicine",
        entity_id=medicine.id,
        actor=user,
        after=_medicine_snapshot(medicine),
        request=request,
    )
    db.commit()
    db.refresh(medicine)
    out = MedicineOut.model_validate(medicine)
    out.expected_stock = expected_stock(db, medicine.id)
    out.packages = [_package_out(db, p) for p in medicine.packages]
    return out


@router.get("/scan")
def scan_barcode(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("medicine:view")),
    barcode: str = Query(min_length=1, max_length=100),
) -> ScanResult:
    """Barcode-first identification. Unknown barcodes are never auto-assigned."""
    row = db.execute(
        select(MedicineBarcode).where(MedicineBarcode.barcode == barcode.strip())
    ).scalar_one_or_none()
    if row is None:
        return ScanResult(
            found=False,
            barcode=barcode.strip(),
            message="Unknown barcode. Search manually or ask an administrator to register it.",
        )
    medicine = db.get(Medicine, row.medicine_id)
    if medicine is None:
        return ScanResult(found=False, barcode=barcode.strip(), message="Barcode points to a missing medicine.")
    out = MedicineOut.model_validate(medicine)
    out.expected_stock = expected_stock(db, medicine.id)
    out.packages = [_package_out(db, p) for p in medicine.packages]
    return ScanResult(found=True, barcode=barcode.strip(), medicine=out)


@router.get("/{medicine_id}", response_model=MedicineOut)
def get_medicine(
    medicine_id: int,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("medicine:view")),
):
    medicine = _get_medicine_or_404(db, medicine_id)
    out = MedicineOut.model_validate(medicine)
    out.expected_stock = expected_stock(db, medicine.id)
    out.packages = [_package_out(db, p) for p in medicine.packages]
    return out


@router.patch("/{medicine_id}", response_model=MedicineOut)
def update_medicine(
    medicine_id: int,
    payload: MedicineUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("medicine:manage")),
):
    medicine = _get_medicine_or_404(db, medicine_id)
    before = _medicine_snapshot(medicine)
    data = payload.model_dump(exclude_unset=True)

    if "opening_stock" in data and data["opening_stock"] != medicine.opening_stock:
        other_txns = db.execute(
            select(func.count())
            .select_from(InventoryTransaction)
            .where(
                InventoryTransaction.medicine_id == medicine.id,
                InventoryTransaction.transaction_type != TransactionType.OPENING.value,
            )
        ).scalar_one()
        if other_txns:
            raise ApiError(
                422,
                "Opening stock cannot be changed after other transactions exist. "
                "Use an inventory adjustment instead.",
                "opening_stock_locked",
            )
        # Replace the opening transaction
        db.query(InventoryTransaction).filter(
            InventoryTransaction.medicine_id == medicine.id,
            InventoryTransaction.transaction_type == TransactionType.OPENING.value,
        ).delete()
        new_opening = Decimal(data["opening_stock"])
        if new_opening > 0:
            db.add(
                InventoryTransaction(
                    medicine_id=medicine.id,
                    transaction_type=TransactionType.OPENING.value,
                    quantity=new_opening,
                    recorded_by=user.id,
                    notes="Opening stock updated",
                )
            )

    for field, value in data.items():
        setattr(medicine, field, value)
    medicine.updated_by = user.id
    db.add(medicine)
    db.flush()
    log_action(
        db,
        action="medicine.update",
        entity_type="medicine",
        entity_id=medicine.id,
        actor=user,
        before=before,
        after=_medicine_snapshot(medicine),
        request=request,
    )
    db.commit()
    db.refresh(medicine)
    out = MedicineOut.model_validate(medicine)
    out.expected_stock = expected_stock(db, medicine.id)
    out.packages = [_package_out(db, p) for p in medicine.packages]
    return out


@router.post("/{medicine_id}/deactivate", response_model=MedicineOut)
def deactivate_medicine(
    medicine_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("medicine:manage")),
):
    medicine = _get_medicine_or_404(db, medicine_id)
    if not medicine.is_active:
        raise ApiError(409, "Medicine is already inactive", "already_inactive")
    before = _medicine_snapshot(medicine)
    medicine.is_active = False
    medicine.updated_by = user.id
    db.add(medicine)
    log_action(
        db,
        action="medicine.deactivate",
        entity_type="medicine",
        entity_id=medicine.id,
        actor=user,
        before=before,
        after=_medicine_snapshot(medicine),
        request=request,
    )
    db.commit()
    db.refresh(medicine)
    out = MedicineOut.model_validate(medicine)
    out.expected_stock = expected_stock(db, medicine.id)
    return out


# ---------------------------------------------------------------------------
# Barcodes
# ---------------------------------------------------------------------------
@router.post("/{medicine_id}/barcodes", response_model=BarcodeOut, status_code=201)
def add_barcode(
    medicine_id: int,
    payload: BarcodeCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("barcode:manage")),
):
    medicine = _get_medicine_or_404(db, medicine_id)
    if not medicine.is_active:
        raise ApiError(422, "Cannot add barcodes to an inactive medicine", "inactive_medicine")
    _validate_barcodes(db, medicine, [payload])
    if payload.is_primary:
        for existing in medicine.barcodes:
            existing.is_primary = False
            db.add(existing)
    row = MedicineBarcode(
        medicine_id=medicine.id,
        barcode=payload.barcode.strip(),
        is_primary=payload.is_primary,
        note=payload.note,
        created_by=user.id,
    )
    try:
        # SAVEPOINT: a uniqueness race must surface as a 409 conflict and must
        # not leave a half-applied primary-barcode switch behind.
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        if _is_barcode_unique_violation(exc):
            _raise_barcode_conflict(db, medicine, payload.barcode.strip())
        raise
    log_action(
        db,
        action="barcode.register",
        entity_type="medicine_barcode",
        entity_id=row.id,
        actor=user,
        after={"medicine_id": medicine.id, "barcode": row.barcode, "is_primary": row.is_primary},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return BarcodeOut.model_validate(row)


@router.delete("/barcodes/{barcode_id}", status_code=204)
def delete_barcode(
    barcode_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("barcode:manage")),
):
    row = db.get(MedicineBarcode, barcode_id)
    if row is None:
        raise ApiError(404, "Barcode not found", "not_found")
    before = {"barcode": row.barcode, "medicine_id": row.medicine_id, "is_primary": row.is_primary}
    db.delete(row)
    log_action(
        db,
        action="barcode.delete",
        entity_type="medicine_barcode",
        entity_id=barcode_id,
        actor=user,
        before=before,
        request=request,
    )
    db.commit()
    return {"detail": "Barcode removed"}


# ---------------------------------------------------------------------------
# Package configurations
# ---------------------------------------------------------------------------
@router.post("/{medicine_id}/packages", response_model=PackageOut, status_code=201)
def create_package(
    medicine_id: int,
    payload: PackageCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("package:manage")),
):
    medicine = _get_medicine_or_404(db, medicine_id)
    if not medicine.is_active:
        raise ApiError(422, "Cannot configure packages for an inactive medicine", "inactive_medicine")
    exists = db.execute(
        select(PackageConfiguration).where(
            PackageConfiguration.medicine_id == medicine.id,
            PackageConfiguration.name == payload.name,
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise ApiError(409, f"Package name already exists: {payload.name}", "duplicate_package")
    validate_package_parent(db, medicine.id, None, payload.parent_id)
    if payload.parent_id is not None:
        parent = db.get(PackageConfiguration, payload.parent_id)
        if parent is None or parent.medicine_id != medicine.id:
            raise ApiError(422, "Parent package does not belong to this medicine", "invalid_parent")
    row = PackageConfiguration(
        medicine_id=medicine.id,
        name=payload.name,
        parent_id=payload.parent_id,
        units_per_parent=payload.units_per_parent,
        sort_order=payload.sort_order,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    log_action(
        db,
        action="package.create",
        entity_type="package_configuration",
        entity_id=row.id,
        actor=user,
        after={
            "medicine_id": medicine.id,
            "name": row.name,
            "parent_id": row.parent_id,
            "units_per_parent": str(row.units_per_parent),
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _package_out(db, row)


@router.put("/packages/{package_id}", response_model=PackageOut)
def update_package(
    package_id: int,
    payload: PackageUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("package:manage")),
):
    row = db.get(PackageConfiguration, package_id)
    if row is None:
        raise ApiError(404, "Package not found", "not_found")
    before = {
        "name": row.name,
        "parent_id": row.parent_id,
        "units_per_parent": str(row.units_per_parent),
    }
    data = payload.model_dump(exclude_unset=True)
    if "parent_id" in data:
        validate_package_parent(db, row.medicine_id, row.id, data["parent_id"])
    for field, value in data.items():
        setattr(row, field, value)
    db.add(row)
    db.flush()
    log_action(
        db,
        action="package.update",
        entity_type="package_configuration",
        entity_id=row.id,
        actor=user,
        before=before,
        after={
            "name": row.name,
            "parent_id": row.parent_id,
            "units_per_parent": str(row.units_per_parent),
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _package_out(db, row)


@router.delete("/packages/{package_id}", status_code=204)
def delete_package(
    package_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("package:manage")),
):
    row = db.get(PackageConfiguration, package_id)
    if row is None:
        raise ApiError(404, "Package not found", "not_found")
    child = db.execute(
        select(func.count()).select_from(PackageConfiguration).where(PackageConfiguration.parent_id == row.id)
    ).scalar_one()
    if child:
        raise ApiError(422, "Remove child packages before deleting this one", "package_has_children")
    before = {"name": row.name, "medicine_id": row.medicine_id}
    db.delete(row)
    log_action(
        db,
        action="package.delete",
        entity_type="package_configuration",
        entity_id=package_id,
        actor=user,
        before=before,
        request=request,
    )
    db.commit()
    return {"detail": "Package removed"}
