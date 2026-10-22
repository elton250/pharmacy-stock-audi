"""Inventory transactions."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.database import get_db, utcnow
from app.deps import require_any_permission, require_permission
from app.errors import ApiError
from app.models.inventory import InventoryTransaction, TransactionType
from app.models.medicines import Medicine
from app.models.users import User
from app.schemas.common import Page
from app.schemas.inventory import InventoryTransactionCreate, InventoryTransactionOut

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


def _txn_out(db: DbSession, row: InventoryTransaction) -> InventoryTransactionOut:
    out = InventoryTransactionOut.model_validate(row)
    medicine = db.get(Medicine, row.medicine_id)
    out.medicine_name = medicine.name if medicine else None
    if row.recorded_by:
        recorder = db.get(User, row.recorded_by)
        out.recorded_by_username = recorder.username if recorder else None
    return out


@router.get("/transactions", response_model=Page[InventoryTransactionOut])
def list_transactions(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_any_permission("inventory:view", "inventory:transact")),
    medicine_id: Optional[int] = Query(default=None),
    transaction_type: Optional[TransactionType] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    query = select(InventoryTransaction)
    count_query = select(func.count()).select_from(InventoryTransaction)
    if medicine_id is not None:
        query = query.where(InventoryTransaction.medicine_id == medicine_id)
        count_query = count_query.where(InventoryTransaction.medicine_id == medicine_id)
    if transaction_type is not None:
        query = query.where(InventoryTransaction.transaction_type == transaction_type.value)
        count_query = count_query.where(InventoryTransaction.transaction_type == transaction_type.value)
    total = db.execute(count_query).scalar_one()
    rows = (
        db.execute(
            query.order_by(InventoryTransaction.occurred_at.desc(), InventoryTransaction.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return Page[InventoryTransactionOut](
        items=[_txn_out(db, r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.post("/transactions", response_model=InventoryTransactionOut, status_code=201)
def create_transaction(
    payload: InventoryTransactionCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("inventory:transact")),
):
    medicine = db.get(Medicine, payload.medicine_id)
    if medicine is None:
        raise ApiError(404, "Medicine not found", "not_found")
    if not medicine.is_active and payload.transaction_type not in {
        TransactionType.ISSUED,
        TransactionType.ADJUSTMENT_NEGATIVE,
        TransactionType.RETURN_OUT,
    }:
        raise ApiError(
            422,
            f"Inactive medicine can only receive issue/return-out/negative adjustment transactions, "
            f"not {payload.transaction_type.value}",
            "inactive_medicine",
        )
    if payload.transaction_type == TransactionType.OPENING:
        raise ApiError(
            422,
            "Opening stock is configured on the medicine record; use adjustments for changes",
            "opening_via_transaction",
        )
    row = InventoryTransaction(
        medicine_id=medicine.id,
        transaction_type=payload.transaction_type.value,
        quantity=payload.quantity,
        occurred_at=payload.occurred_at or utcnow(),
        recorded_by=user.id,
        reference_number=payload.reference_number,
        notes=payload.notes,
    )
    db.add(row)
    db.flush()
    log_action(
        db,
        action="inventory.transaction",
        entity_type="inventory_transaction",
        entity_id=row.id,
        actor=user,
        after={
            "medicine_id": medicine.id,
            "type": row.transaction_type,
            "quantity": str(row.quantity),
            "reference_number": row.reference_number,
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _txn_out(db, row)
