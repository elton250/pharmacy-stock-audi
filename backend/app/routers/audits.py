"""Physical stock audits and variance computation."""
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.database import get_db, utcnow
from app.deps import require_any_permission, require_permission
from app.errors import ApiError
from app.models.audits import StockAudit, StockAuditItem, StockAuditStatus, VarianceClassification
from app.models.counting import CountingSession, SessionStatus
from app.models.inventory import SIGN, InventoryTransaction, TransactionType
from app.models.medicines import Medicine
from app.models.users import User
from app.schemas.audits import AuditItemAdd, StockAuditCreate, StockAuditItemOut, StockAuditOut
from app.schemas.common import Page
from app.services.codes import generate_unique_code
from app.services.counting import (
    classify_variance,
    count_operator_for_session,
    latest_completed_session,
    physical_stock_from_completed_counts,
)
from app.services.stock import expected_stock

router = APIRouter(prefix="/api/audits", tags=["stock-audits"])


def _item_out(db: DbSession, item: StockAuditItem) -> StockAuditItemOut:
    out = StockAuditItemOut.model_validate(item)
    if item.medicine:
        out.medicine_name = item.medicine.name
        out.medicine_generic = item.medicine.generic_name
    if item.counted_by:
        u = db.get(User, item.counted_by)
        out.counter_username = u.username if u else None
    if item.reviewed_by:
        u = db.get(User, item.reviewed_by)
        out.reviewer_username = u.username if u else None
    return out


def _audit_out(db: DbSession, row: StockAudit) -> StockAuditOut:
    out = StockAuditOut.model_validate(row)
    if row.initiated_by:
        u = db.get(User, row.initiated_by)
        out.initiator_username = u.username if u else None
    out.items = [_item_out(db, i) for i in row.items]
    return out


def _refresh_item(db: DbSession, item: StockAuditItem) -> None:
    item.expected_stock = expected_stock(db, item.medicine_id)
    physical = physical_stock_from_completed_counts(db, item.medicine_id, stock_audit_id=item.stock_audit_id)
    if physical is None:
        # No completed, non-archived counting session for this medicine (yet):
        # the item stays UNCOUNTED instead of being reported as a false shortage.
        item.physical_stock = None
        item.variance = None
        item.classification = VarianceClassification.UNCOUNTED.value
        db.add(item)
        return
    item.physical_stock = physical
    item.variance = physical - item.expected_stock
    item.classification = classify_variance(physical, item.expected_stock).value
    # Keep the counter identity in step with the count that produced the
    # physical stock: the operator of that count, never the reviewer.
    session_row = latest_completed_session(
        db, item.medicine_id, stock_audit_id=item.stock_audit_id
    )
    if session_row is not None:
        operator_id = count_operator_for_session(db, session_row)
        if operator_id is not None:
            item.counted_by = operator_id
    db.add(item)


@router.get("", response_model=Page[StockAuditOut])
def list_audits(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_any_permission("audit:view", "audit:initiate")),
    status: Optional[StockAuditStatus] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    query = select(StockAudit)
    count_query = select(func.count()).select_from(StockAudit)
    if status is not None:
        query = query.where(StockAudit.status == status.value)
        count_query = count_query.where(StockAudit.status == status.value)
    total = db.execute(count_query).scalar_one()
    rows = (
        db.execute(query.order_by(StockAudit.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return Page[StockAuditOut](
        items=[_audit_out(db, r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=StockAuditOut, status_code=201)
def create_audit(
    payload: StockAuditCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("audit:initiate")),
):
    code = generate_unique_code(db, StockAudit, "SA")
    audit = StockAudit(
        code=code,
        title=payload.title,
        audit_date=payload.audit_date or datetime.now(timezone.utc).date(),
        notes=payload.notes,
        initiated_by=user.id,
    )
    db.add(audit)
    db.flush()
    for medicine_id in dict.fromkeys(payload.medicine_ids):
        medicine = db.get(Medicine, medicine_id)
        if medicine is None:
            raise ApiError(404, f"Medicine {medicine_id} not found", "not_found")
        db.add(
            StockAuditItem(
                stock_audit_id=audit.id,
                medicine_id=medicine.id,
                expected_stock=expected_stock(db, medicine.id),
            )
        )
    db.flush()
    log_action(
        db,
        action="audit.create",
        entity_type="stock_audit",
        entity_id=audit.id,
        actor=user,
        after={"code": code, "title": audit.title, "medicine_ids": payload.medicine_ids},
        request=request,
    )
    db.commit()
    db.refresh(audit)
    return _audit_out(db, audit)


@router.get("/{audit_id}", response_model=StockAuditOut)
def get_audit(
    audit_id: int,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_any_permission("audit:view", "audit:initiate", "audit:manage")),
):
    audit = db.get(StockAudit, audit_id)
    if audit is None:
        raise ApiError(404, "Stock audit not found", "not_found")
    # Refresh variance data for open audits so the report is always current.
    if audit.status == StockAuditStatus.OPEN.value:
        for item in audit.items:
            _refresh_item(db, item)
        db.commit()
        db.refresh(audit)
    return _audit_out(db, audit)


@router.post("/{audit_id}/items", response_model=StockAuditOut)
def add_items(
    audit_id: int,
    payload: AuditItemAdd,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("audit:initiate")),
):
    audit = db.get(StockAudit, audit_id)
    if audit is None:
        raise ApiError(404, "Stock audit not found", "not_found")
    if audit.status != StockAuditStatus.OPEN.value:
        raise ApiError(409, "Audit is not open", "audit_not_open")
    existing = {i.medicine_id for i in audit.items}
    added = []
    for medicine_id in dict.fromkeys(payload.medicine_ids):
        if medicine_id in existing:
            continue
        medicine = db.get(Medicine, medicine_id)
        if medicine is None:
            raise ApiError(404, f"Medicine {medicine_id} not found", "not_found")
        db.add(
            StockAuditItem(
                stock_audit_id=audit.id,
                medicine_id=medicine.id,
                expected_stock=expected_stock(db, medicine.id),
            )
        )
        added.append(medicine_id)
    db.flush()
    log_action(
        db,
        action="audit.add_items",
        entity_type="stock_audit",
        entity_id=audit.id,
        actor=user,
        after={"added_medicine_ids": added},
        request=request,
    )
    db.commit()
    db.refresh(audit)
    return _audit_out(db, audit)


@router.post("/{audit_id}/link-session")
def link_counting_session(
    audit_id: int,
    payload: dict,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("audit:initiate")),
):
    """Link a counting session (DRAFT/IN_PROGRESS) to this audit for a medicine in scope."""
    session_id = payload.get("session_id")
    if not isinstance(session_id, int):
        raise ApiError(422, "session_id (int) is required", "invalid_payload")
    audit = db.get(StockAudit, audit_id)
    if audit is None:
        raise ApiError(404, "Stock audit not found", "not_found")
    if audit.status != StockAuditStatus.OPEN.value:
        raise ApiError(409, "Audit is not open", "audit_not_open")
    session_row = db.get(CountingSession, session_id)
    if session_row is None:
        raise ApiError(404, "Counting session not found", "not_found")
    if session_row.status not in {SessionStatus.DRAFT.value, SessionStatus.IN_PROGRESS.value}:
        raise ApiError(409, "Only draft or in-progress sessions can be linked", "invalid_session_state")
    if session_row.active_medicine_id is not None and not any(
        i.medicine_id == session_row.active_medicine_id for i in audit.items
    ):
        raise ApiError(422, "Session medicine is not in the audit scope", "medicine_not_in_audit")
    session_row.stock_audit_id = audit.id
    db.add(session_row)
    log_action(
        db,
        action="audit.link_session",
        entity_type="stock_audit",
        entity_id=audit.id,
        actor=user,
        after={"session_id": session_id},
        request=request,
    )
    db.commit()
    return {"detail": "Session linked to audit"}


@router.post("/{audit_id}/complete", response_model=StockAuditOut)
def complete_audit(
    audit_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("audit:manage")),
):
    audit = db.get(StockAudit, audit_id)
    if audit is None:
        raise ApiError(404, "Stock audit not found", "not_found")
    if audit.status != StockAuditStatus.OPEN.value:
        raise ApiError(409, "Audit is not open", "audit_not_open")
    open_sessions = db.execute(
        select(func.count())
        .select_from(CountingSession)
        .where(
            CountingSession.stock_audit_id == audit.id,
            CountingSession.status.in_(
                [SessionStatus.DRAFT.value, SessionStatus.IN_PROGRESS.value, SessionStatus.PENDING_REVIEW.value]
            ),
        )
    ).scalar_one()
    if open_sessions:
        raise ApiError(
            409,
            f"Cannot complete audit while {open_sessions} counting session(s) linked to it are still open",
            "sessions_open",
        )
    for item in audit.items:
        _refresh_item(db, item)
        if item.physical_stock is None:
            item.classification = VarianceClassification.UNCOUNTED.value
            db.add(item)
    audit.status = StockAuditStatus.COMPLETED.value
    audit.completed_by = user.id
    audit.completed_at = utcnow()
    db.add(audit)
    log_action(
        db,
        action="audit.complete",
        entity_type="stock_audit",
        entity_id=audit.id,
        actor=user,
        after={"status": audit.status},
        request=request,
    )
    db.commit()
    db.refresh(audit)
    return _audit_out(db, audit)


@router.post("/{audit_id}/cancel", response_model=StockAuditOut)
def cancel_audit(
    audit_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("audit:manage")),
):
    audit = db.get(StockAudit, audit_id)
    if audit is None:
        raise ApiError(404, "Stock audit not found", "not_found")
    if audit.status != StockAuditStatus.OPEN.value:
        raise ApiError(409, "Audit is not open", "audit_not_open")
    audit.status = StockAuditStatus.CANCELLED.value
    audit.cancelled_at = utcnow()
    db.add(audit)
    log_action(
        db,
        action="audit.cancel",
        entity_type="stock_audit",
        entity_id=audit.id,
        actor=user,
        after={"status": audit.status},
        request=request,
    )
    db.commit()
    db.refresh(audit)
    return _audit_out(db, audit)
