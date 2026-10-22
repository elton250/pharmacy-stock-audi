"""Variance and stock-level reports."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.database import get_db
from app.deps import require_permission
from app.errors import ApiError
from app.models.audits import StockAudit, StockAuditItem, VarianceClassification
from app.models.counting import CountEvent, CountingSession, SessionStatus
from app.models.inventory import TransactionType
from app.models.medicines import Medicine
from app.models.users import User
from app.schemas.audits import StockLevelRow, VarianceReportRow
from app.schemas.common import Page
from app.services.counting import classify_variance, physical_stock_from_completed_counts
from app.services.stock import expected_stock, expected_stock_breakdown

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/variance", response_model=Page[VarianceReportRow])
def variance_report(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("report:view")),
    audit_id: Optional[int] = Query(default=None),
    medicine_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """Expected vs physical stock with shortage/surplus classification."""
    audit: Optional[StockAudit] = None
    items_query = select(StockAuditItem)
    if audit_id is not None:
        audit = db.get(StockAudit, audit_id)
        if audit is None:
            raise ApiError(404, "Stock audit not found", "not_found")
        items_query = items_query.where(StockAuditItem.stock_audit_id == audit.id)

    if audit is not None:
        items = db.execute(items_query.order_by(StockAuditItem.id)).scalars().all()
        rows: list[VarianceReportRow] = []
        for item in items:
            medicine = db.get(Medicine, item.medicine_id)
            if medicine is None:
                continue
            if medicine_id is not None and medicine.id != medicine_id:
                continue
            breakdown = expected_stock_breakdown(db, medicine.id)
            # Find the completed counting session (linked to this audit) for operator info
            session_row = db.execute(
                select(CountingSession)
                .where(
                    CountingSession.stock_audit_id == audit.id,
                    CountingSession.active_medicine_id == medicine.id,
                    CountingSession.status == SessionStatus.COMPLETED.value,
                    CountingSession.archived_at.is_(None),
                )
                .order_by(CountingSession.completed_at.desc())
            ).scalars().first()
            operator_name = None
            reviewer_name = None
            session_code = None
            if session_row is not None:
                session_code = session_row.code
                if session_row.created_by:
                    u = db.get(User, session_row.created_by)
                    operator_name = u.username if u else None
                if session_row.reviewed_by:
                    u = db.get(User, session_row.reviewed_by)
                    reviewer_name = u.username if u else None
            physical = item.physical_stock
            variance = item.variance
            classification = item.classification or VarianceClassification.UNCOUNTED.value
            rows.append(
                VarianceReportRow(
                    medicine_id=medicine.id,
                    medicine_name=medicine.name,
                    generic_name=medicine.generic_name,
                    base_unit=medicine.base_unit,
                    opening_stock=breakdown.get(TransactionType.OPENING.value, Decimal("0")),
                    received=breakdown.get(TransactionType.RECEIVED.value, Decimal("0")),
                    issued=breakdown.get(TransactionType.ISSUED.value, Decimal("0")),
                    returns_in=breakdown.get(TransactionType.RETURN_IN.value, Decimal("0")),
                    returns_out=breakdown.get(TransactionType.RETURN_OUT.value, Decimal("0")),
                    adjustments_positive=breakdown.get(TransactionType.ADJUSTMENT_POSITIVE.value, Decimal("0")),
                    adjustments_negative=breakdown.get(TransactionType.ADJUSTMENT_NEGATIVE.value, Decimal("0")),
                    expected_stock=item.expected_stock,
                    physical_stock=physical,
                    variance=variance,
                    classification=classification,
                    counting_operator=operator_name,
                    reviewer=reviewer_name,
                    audit_date=audit.audit_date if audit else None,
                    counting_session_code=session_code,
                    notes=item.notes,
                )
            )
    else:
        query = select(Medicine)
        if medicine_id is not None:
            query = query.where(Medicine.id == medicine_id)
        medicines = db.execute(query.order_by(Medicine.name)).scalars().all()
        rows = []
        for medicine in medicines:
            breakdown = expected_stock_breakdown(db, medicine.id)
            expected = expected_stock(db, medicine.id)
            physical = physical_stock_from_completed_counts(db, medicine.id)
            session_row = db.execute(
                select(CountingSession)
                .where(
                    CountingSession.active_medicine_id == medicine.id,
                    CountingSession.status == SessionStatus.COMPLETED.value,
                    CountingSession.archived_at.is_(None),
                )
                .order_by(CountingSession.completed_at.desc())
            ).scalars().first()
            operator_name = reviewer_name = session_code = None
            if session_row is not None:
                session_code = session_row.code
                u = db.get(User, session_row.created_by)
                operator_name = u.username if u else None
                if session_row.reviewed_by:
                    ru = db.get(User, session_row.reviewed_by)
                    reviewer_name = ru.username if ru else None
            variance = None if physical is None else physical - expected
            classification = (
                classify_variance(physical, expected).value
                if physical is not None
                else VarianceClassification.UNCOUNTED.value
            )
            rows.append(
                VarianceReportRow(
                    medicine_id=medicine.id,
                    medicine_name=medicine.name,
                    generic_name=medicine.generic_name,
                    base_unit=medicine.base_unit,
                    opening_stock=breakdown.get(TransactionType.OPENING.value, Decimal("0")),
                    received=breakdown.get(TransactionType.RECEIVED.value, Decimal("0")),
                    issued=breakdown.get(TransactionType.ISSUED.value, Decimal("0")),
                    returns_in=breakdown.get(TransactionType.RETURN_IN.value, Decimal("0")),
                    returns_out=breakdown.get(TransactionType.RETURN_OUT.value, Decimal("0")),
                    adjustments_positive=breakdown.get(TransactionType.ADJUSTMENT_POSITIVE.value, Decimal("0")),
                    adjustments_negative=breakdown.get(TransactionType.ADJUSTMENT_NEGATIVE.value, Decimal("0")),
                    expected_stock=expected,
                    physical_stock=physical,
                    variance=variance,
                    classification=classification,
                    counting_operator=operator_name,
                    reviewer=reviewer_name,
                    counting_session_code=session_code,
                )
            )

    total = len(rows)
    start = (page - 1) * page_size
    return Page[VarianceReportRow](
        items=rows[start : start + page_size], total=total, page=page, page_size=page_size
    )


@router.get("/stock-levels", response_model=Page[StockLevelRow])
def stock_levels(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("report:view")),
    include_inactive: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    query = select(Medicine)
    if not include_inactive:
        query = query.where(Medicine.is_active.is_(True))
    medicines = db.execute(query.order_by(Medicine.name)).scalars().all()
    rows: list[StockLevelRow] = []
    for m in medicines:
        expected = expected_stock(db, m.id)
        rows.append(
            StockLevelRow(
                medicine_id=m.id,
                medicine_name=m.name,
                base_unit=m.base_unit,
                expected_stock=expected,
                reorder_level=m.reorder_level,
                below_reorder=expected <= m.reorder_level,
                is_active=m.is_active,
            )
        )
    total = len(rows)
    start = (page - 1) * page_size
    return Page[StockLevelRow](
        items=rows[start : start + page_size], total=total, page=page, page_size=page_size
    )


@router.get("/count-events/{session_id}")
def count_events_report(
    session_id: int,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("report:view")),
):
    session_row = db.get(CountingSession, session_id)
    if session_row is None:
        raise ApiError(404, "Counting session not found", "not_found")
    events = db.execute(
        select(CountEvent).where(CountEvent.session_id == session_id).order_by(CountEvent.created_at)
    ).scalars().all()
    return {
        "session_code": session_row.code,
        "status": session_row.status,
        "events": [
            {
                "id": e.id,
                "medicine_id": e.medicine_id,
                "operator_id": e.operator_id,
                "total_base_units": str(e.total_base_units),
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }
