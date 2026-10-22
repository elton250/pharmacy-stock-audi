"""Strict static counting: sessions, count events, review and completion."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.database import get_db, utcnow
from app.deps import require_any_permission, require_permission
from app.errors import ApiError
from app.models.audits import StockAudit, StockAuditStatus
from app.models.counting import (
    CountEvent,
    CountEventLine,
    CountingSession,
    ReviewDecision,
    SessionStatus,
)
from app.models.medicines import Medicine, PackageConfiguration
from app.models.users import User
from app.permissions import user_permission_codes
from app.schemas.common import Page
from app.schemas.counting import (
    ActivateRequest,
    CancelSessionRequest,
    CountEventLineOut,
    CountEventOut,
    CountSubmitRequest,
    CountingSessionCreate,
    CountingSessionOut,
    ReviewRequest,
)
from app.services.codes import generate_unique_code
from app.services.counting import (
    ACTIVE_SESSION_STATUSES,
    assert_transition,
    complete_review,
    get_active_session,
    lock_active_session_slot,
    physical_stock_from_completed_counts,
    require_session_status,
)
from app.services.packages import base_units_for_line, package_units_map

router = APIRouter(prefix="/api/counting", tags=["counting"])


def _get_session_or_404(db: DbSession, session_id: int) -> CountingSession:
    row = db.get(CountingSession, session_id)
    if row is None:
        raise ApiError(404, "Counting session not found", "not_found")
    return row


def _primary_barcode(medicine: Optional[Medicine]) -> Optional[str]:
    """Primary barcode of a medicine, falling back to the first registered one."""
    if medicine is None or not medicine.barcodes:
        return None
    for barcode in medicine.barcodes:
        if barcode.is_primary:
            return barcode.barcode
    return medicine.barcodes[0].barcode


def _session_out(db: DbSession, row: CountingSession) -> CountingSessionOut:
    out = CountingSessionOut.model_validate(row)
    if row.active_medicine_id:
        medicine = db.get(Medicine, row.active_medicine_id)
        out.active_medicine_name = medicine.name if medicine else None
        out.active_medicine_barcode = _primary_barcode(medicine)
    if row.created_by:
        creator = db.get(User, row.created_by)
        out.creator_username = creator.username if creator else None
    if row.reviewed_by:
        reviewer = db.get(User, row.reviewed_by)
        out.reviewer_username = reviewer.username if reviewer else None
    if row.archived_by:
        archiver = db.get(User, row.archived_by)
        out.archived_by_username = archiver.username if archiver else None
    if row.cancelled_by:
        canceller = db.get(User, row.cancelled_by)
        out.cancelled_by_username = canceller.username if canceller else None
    out.total_counted_base_units = sum((e.total_base_units for e in row.events), Decimal("0"))
    event_outs: list[CountEventOut] = []
    for event in row.events:
        ev = CountEventOut.model_validate(event)
        operator = db.get(User, event.operator_id)
        ev.operator_username = operator.username if operator else None
        line_outs: list[CountEventLineOut] = []
        for line in event.lines:
            lo = CountEventLineOut.model_validate(line)
            if line.package_config_id:
                pkg = db.get(PackageConfiguration, line.package_config_id)
                lo.package_name = pkg.name if pkg else None
            line_outs.append(lo)
        ev.lines = line_outs
        event_outs.append(ev)
    out.events = event_outs
    return out


@router.get("/sessions", response_model=Page[CountingSessionOut])
def list_sessions(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_any_permission("count:perform", "count:review")),
    status: Optional[SessionStatus] = Query(default=None),
    mine: bool = Query(default=False),
    include_archived: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user: User = Depends(require_permission("count:perform")),
):
    if include_archived and "count:manage" not in user_permission_codes(user):
        raise ApiError(403, "Missing required permission: count:manage", "forbidden")
    query = select(CountingSession)
    count_query = select(func.count()).select_from(CountingSession)
    if not include_archived:
        query = query.where(CountingSession.archived_at.is_(None))
        count_query = count_query.where(CountingSession.archived_at.is_(None))
    if status is not None:
        query = query.where(CountingSession.status == status.value)
        count_query = count_query.where(CountingSession.status == status.value)
    if mine:
        query = query.where(CountingSession.created_by == user.id)
        count_query = count_query.where(CountingSession.created_by == user.id)
    total = db.execute(count_query).scalar_one()
    rows = (
        db.execute(
            query.order_by(CountingSession.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return Page[CountingSessionOut](
        items=[_session_out(db, r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.post("/sessions", response_model=CountingSessionOut, status_code=201)
def create_session(
    payload: CountingSessionCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:perform")),
):
    medicine = None
    if payload.medicine_id is not None:
        medicine = db.get(Medicine, payload.medicine_id)
        if medicine is None:
            raise ApiError(404, "Medicine not found", "not_found")
        if not medicine.is_active:
            raise ApiError(422, "Cannot start counting an inactive medicine", "inactive_medicine")

    audit = None
    if payload.stock_audit_id is not None:
        audit = db.get(StockAudit, payload.stock_audit_id)
        if audit is None:
            raise ApiError(404, "Stock audit not found", "not_found")
        if audit.status != StockAuditStatus.OPEN.value:
            raise ApiError(422, "Stock audit is not open", "audit_not_open")

    # Enforce: only ONE active static counting session may exist across the system.
    # The advisory lock serialises concurrent creations (different tabs / double
    # clicks) so the check below is deterministic; the partial unique index
    # ``uq_counting_sessions_single_active`` stays the authoritative guarantee.
    lock_active_session_slot(db)
    try:
        with db.begin_nested():
            active = get_active_session(db, for_update=True)
            if active is not None:
                raise ApiError(
                    409,
                    f"Another counting session must be completed or cancelled first "
                    f"(active session: {active.code}, status: {active.status}, "
                    f"medicine_id: {active.active_medicine_id}).",
                    "active_session_exists",
                )
    except ApiError:
        raise
    except Exception:
        db.rollback()
        raise

    code = generate_unique_code(db, CountingSession, "CS")
    status = SessionStatus.IN_PROGRESS if medicine is not None else SessionStatus.DRAFT
    row = CountingSession(
        code=code,
        status=status.value,
        active_medicine_id=medicine.id if medicine else None,
        stock_audit_id=audit.id if audit else None,
        created_by=user.id,
        notes=payload.notes,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(
            409,
            "Another counting session must be completed or cancelled first.",
            "active_session_exists",
        )
    log_action(
        db,
        action="counting.session_create",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        after={"code": code, "status": row.status, "active_medicine_id": row.active_medicine_id},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.get("/sessions/active", response_model=CountingSessionOut | None)
def get_active_session_endpoint(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_any_permission("count:perform", "count:review")),
):
    """The single active counting session, or null when nothing is open."""
    active = get_active_session(db, for_update=False)
    if active is None:
        return None
    return _session_out(db, active)


@router.get("/sessions/{session_id}", response_model=CountingSessionOut)
def get_session(
    session_id: int,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_any_permission("count:perform", "count:review", "report:view")),
):
    row = _get_session_or_404(db, session_id)
    return _session_out(db, row)


@router.post("/sessions/{session_id}/activate", response_model=CountingSessionOut)
def activate_medicine(
    session_id: int,
    payload: ActivateRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:perform")),
):
    """Explicitly activate the single medicine for a DRAFT session. Never auto-switches."""
    row = db.execute(
        select(CountingSession).where(CountingSession.id == session_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "Counting session not found", "not_found")
    require_session_status(row, SessionStatus.DRAFT)
    if row.created_by != user.id:
        raise ApiError(403, "Only the session creator can activate its medicine", "not_session_creator")
    # Enforce single active session across system.
    active = get_active_session(db, for_update=True)
    if active is not None and active.id != row.id:
        raise ApiError(
            409,
            f"Another counting session must be completed or cancelled first ({active.code}).",
            "active_session_exists",
        )
    medicine = db.get(Medicine, payload.medicine_id)
    if medicine is None:
        raise ApiError(404, "Medicine not found", "not_found")
    if not medicine.is_active:
        raise ApiError(422, "Cannot count an inactive medicine", "inactive_medicine")
    # Medicine ID is immutable once set; if already set on this session it must match.
    if row.active_medicine_id is not None and row.active_medicine_id != medicine.id:
        raise ApiError(
            409,
            "This session is already associated with a different medicine and cannot be switched.",
            "medicine_mismatch",
        )
    assert_transition(row.status, SessionStatus.IN_PROGRESS.value)
    row.active_medicine_id = medicine.id
    row.status = SessionStatus.IN_PROGRESS.value
    db.add(row)
    log_action(
        db,
        action="counting.activate_medicine",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        after={"active_medicine_id": medicine.id},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.post("/sessions/{session_id}/counts", response_model=CountEventOut, status_code=201)
def submit_count(
    session_id: int,
    payload: CountSubmitRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:perform")),
):
    """Record a count event for the session's ACTIVE medicine only (static counting)."""
    row = _get_session_or_404(db, session_id)
    require_session_status(row, SessionStatus.IN_PROGRESS)
    if row.created_by != user.id:
        raise ApiError(403, "Only the session creator can record counts", "not_session_creator")

    # Idempotency: return the existing event when the key was already used.
    existing = db.execute(
        select(CountEvent).where(CountEvent.idempotency_key == payload.idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.session_id != row.id:
            raise ApiError(409, "Idempotency key was already used for another session", "idempotency_conflict")
        ev = CountEventOut.model_validate(existing)
        ev.operator_username = user.username
        ev.lines = [CountEventLineOut.model_validate(l) for l in existing.lines]
        return ev

    if row.active_medicine_id is None:
        raise ApiError(409, "Session has no active medicine", "no_active_medicine")
    # STRICT STATIC COUNTING: reject any attempt to count a different medicine.
    if payload.medicine_id is not None and payload.medicine_id != row.active_medicine_id:
        raise ApiError(
            409,
            "A different medicine is active in this session. Complete, review or cancel "
            "the session before counting another medicine.",
            "medicine_mismatch",
        )

    medicine_id = row.active_medicine_id
    by_id = package_units_map(db, medicine_id)
    total = Decimal("0")
    line_rows: list[CountEventLine] = []
    for line in payload.lines:
        package = None
        if line.package_config_id is not None:
            package = by_id.get(line.package_config_id)
            if package is None:
                raise ApiError(
                    422,
                    f"Package {line.package_config_id} does not belong to this medicine",
                    "package_mismatch",
                )
        if package is None and line.quantity == 0 and (line.partial_units is None or line.partial_units == 0):
            raise ApiError(422, "Each count line must record a positive quantity", "empty_line")
        if package is not None and line.quantity == 0 and line.partial_units is None:
            raise ApiError(422, "Each count line must record a positive quantity", "empty_line")
        base, is_partial = base_units_for_line(package, line.quantity, line.partial_units, by_id)
        if base <= 0:
            raise ApiError(422, "Count line converts to zero base units", "empty_line")
        total += base
        line_rows.append(
            CountEventLine(
                package_config_id=package.id if package else None,
                quantity=line.quantity,
                partial_units=line.partial_units,
                base_units=base,
                is_partial_package=is_partial,
            )
        )

    event = CountEvent(
        session_id=row.id,
        medicine_id=medicine_id,
        operator_id=user.id,
        total_base_units=total.quantize(Decimal("0.001")),
        idempotency_key=payload.idempotency_key,
        notes=payload.notes,
    )
    event.lines = line_rows
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "Duplicate submission rejected (idempotency key already used)", "duplicate_submission")
    log_action(
        db,
        action="counting.count_event",
        entity_type="count_event",
        entity_id=event.id,
        actor=user,
        after={
            "session_id": row.id,
            "medicine_id": medicine_id,
            "total_base_units": str(event.total_base_units),
            "lines": [
                {
                    "package_config_id": l.package_config_id,
                    "quantity": str(l.quantity),
                    "partial_units": str(l.partial_units) if l.partial_units is not None else None,
                    "base_units": str(l.base_units),
                }
                for l in line_rows
            ],
        },
        request=request,
    )
    db.commit()
    db.refresh(event)
    ev = CountEventOut.model_validate(event)
    ev.operator_username = user.username
    ev.lines = []
    for line in event.lines:
        lo = CountEventLineOut.model_validate(line)
        if line.package_config_id:
            pkg = db.get(PackageConfiguration, line.package_config_id)
            lo.package_name = pkg.name if pkg else None
        ev.lines.append(lo)
    return ev


@router.post("/sessions/{session_id}/submit", response_model=CountingSessionOut)
def submit_for_review(
    session_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:perform")),
):
    row = _get_session_or_404(db, session_id)
    require_session_status(row, SessionStatus.IN_PROGRESS)
    if row.created_by != user.id:
        raise ApiError(403, "Only the session creator can submit it for review", "not_session_creator")
    event_count = db.execute(
        select(func.count()).select_from(CountEvent).where(CountEvent.session_id == row.id)
    ).scalar_one()
    if event_count == 0:
        raise ApiError(422, "Cannot submit a session without any count events", "no_counts")
    assert_transition(row.status, SessionStatus.PENDING_REVIEW.value)
    row.status = SessionStatus.PENDING_REVIEW.value
    row.submitted_at = utcnow()
    row.submitted_by = user.id
    db.add(row)
    log_action(
        db,
        action="counting.submit",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        after={"status": row.status},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.post("/sessions/{session_id}/review", response_model=CountingSessionOut)
def review_session(
    session_id: int,
    payload: ReviewRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:review")),
):
    row = _get_session_or_404(db, session_id)
    complete_review(db, row, payload.decision, user, payload.notes)
    log_action(
        db,
        action="counting.review",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        after={"decision": payload.decision.value, "status": row.status, "notes": payload.notes},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.post("/sessions/{session_id}/cancel", response_model=CountingSessionOut)
def cancel_session(
    session_id: int,
    request: Request,
    payload: Optional[CancelSessionRequest] = None,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_any_permission("count:perform", "count:manage")),
):
    """Cancel an open counting session.

    Authorization: the session creator, or any holder of ``count:manage``
    (supervisors). This is what makes it possible to release a session that
    blocks the whole system (for example when the operator who started it is
    unavailable), without allowing unrelated operators to cancel each other's
    work.

    The cancellation is applied atomically: status, timestamp, actor and the
    audit-log entry are written in the same transaction, so the
    one-active-session rule is only released once the cancellation succeeded.
    """
    lock_active_session_slot(db)
    row = db.execute(
        select(CountingSession).where(CountingSession.id == session_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "Counting session not found", "not_found")
    if row.archived_at is not None:
        raise ApiError(409, "Counting session is archived and can no longer be cancelled", "archived")
    if row.status == SessionStatus.COMPLETED.value:
        raise ApiError(
            409,
            "Counting session is already completed and cannot be cancelled",
            "already_completed",
        )
    if row.status == SessionStatus.CANCELLED.value:
        raise ApiError(
            409,
            "Counting session is already cancelled",
            "already_cancelled",
        )
    require_session_status(row, SessionStatus.DRAFT, SessionStatus.IN_PROGRESS, SessionStatus.PENDING_REVIEW)
    permissions = user_permission_codes(user)
    if row.created_by != user.id and "count:manage" not in permissions:
        raise ApiError(
            403,
            "Only the session creator or a user with the count:manage permission can cancel it",
            "forbidden",
        )
    assert_transition(row.status, SessionStatus.CANCELLED.value)
    reason = payload.reason if payload else None
    row.status = SessionStatus.CANCELLED.value
    row.cancelled_at = utcnow()
    row.cancelled_by = user.id
    db.add(row)
    log_action(
        db,
        action="counting.cancel",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        after={
            "status": row.status,
            "cancelled_at": row.cancelled_at.isoformat(),
            "cancelled_by": row.cancelled_by,
            "reason": reason,
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.post("/sessions/{session_id}/resume", response_model=CountingSessionOut)
def resume_session(
    session_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_any_permission("count:perform", "count:review", "count:manage")),
):
    """Reopen the existing counting session so work can continue.

    Resume NEVER creates a new session, never duplicates count events or
    inventory transactions, never resets the quantities recorded so far and
    never discards previous counting progress: the very same session row and
    its saved count events are returned.
    """
    row = _get_session_or_404(db, session_id)
    if row.archived_at is not None:
        raise ApiError(409, "Counting session is archived and cannot be resumed", "archived")
    if row.status == SessionStatus.COMPLETED.value:
        raise ApiError(
            409,
            "Counting session is already completed and cannot be resumed",
            "already_completed",
        )
    if row.status == SessionStatus.CANCELLED.value:
        raise ApiError(
            409,
            "Counting session is already cancelled and cannot be resumed",
            "already_cancelled",
        )
    if row.status not in ACTIVE_SESSION_STATUSES:
        raise ApiError(
            409,
            f"Counting session cannot be resumed from state {row.status}",
            "invalid_state",
        )
    permissions = user_permission_codes(user)
    if row.created_by != user.id and not permissions.intersection({"count:manage", "count:review"}):
        raise ApiError(
            403,
            "Only the session creator or a user with the count:manage permission can resume it",
            "forbidden",
        )
    # Resuming must never produce a second active session. The advisory lock is
    # taken before the row lock (same order as create/cancel) so concurrent
    # requests cannot deadlock, and the row lock pins the session we verified.
    lock_active_session_slot(db)
    db.execute(
        select(CountingSession).where(CountingSession.id == session_id).with_for_update()
    ).scalar_one_or_none()
    active = get_active_session(db, for_update=True)
    if active is not None and active.id != row.id:
        raise ApiError(
            409,
            f"Another counting session is already active ({active.code}). "
            f"Complete, review or cancel it before resuming this one.",
            "active_session_exists",
        )
    log_action(
        db,
        action="counting.session_resume",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        after={"status": row.status, "total_counted_base_units": str(
            sum((e.total_base_units for e in row.events), Decimal("0"))
        )},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.get("/sessions/{session_id}/physical-stock")
def session_physical_stock(
    session_id: int,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_any_permission("count:perform", "count:review", "report:view")),
):
    row = _get_session_or_404(db, session_id)
    if row.archived_at is not None:
        raise ApiError(409, "Cannot compute physical stock for archived sessions", "archived")
    if row.active_medicine_id is None:
        raise ApiError(409, "Session has no active medicine", "no_active_medicine")
    total = physical_stock_from_completed_counts(db, row.active_medicine_id)
    return {
        "medicine_id": row.active_medicine_id,
        "physical_stock": total,
        "from_completed_sessions": True,
    }
