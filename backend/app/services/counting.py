"""Counting-session state machine and physical-stock aggregation."""
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from app.database import utcnow
from app.errors import ApiError
from app.models.audits import StockAudit, StockAuditItem, StockAuditStatus, VarianceClassification
from app.models.counting import (
    CountEvent,
    CountingSession,
    ReviewDecision,
    SessionStatus,
)
from app.models.users import User
from app.services.stock import expected_stock

ACTIVE_SESSION_STATUSES = {SessionStatus.DRAFT.value, SessionStatus.IN_PROGRESS.value, SessionStatus.PENDING_REVIEW.value}

# Advisory-lock key used to serialise "create a counting session" and "cancel a
# counting session" across concurrent requests. The partial unique index
# ``uq_counting_sessions_single_active`` remains the authoritative database
# guarantee; this lock only makes the API answer deterministic instead of
# relying on an IntegrityError raised at flush time.
ACTIVE_SESSION_LOCK_KEY = 7_310_552_011


def lock_active_session_slot(db: DbSession) -> None:
    """Take a transaction-scoped advisory lock guarding the single active session.

    The lock is released automatically when the surrounding transaction commits
    or rolls back, so it can never be leaked by a crashed request.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": ACTIVE_SESSION_LOCK_KEY},
    )


VALID_TRANSITIONS: dict[str, set[str]] = {
    SessionStatus.DRAFT.value: {SessionStatus.IN_PROGRESS.value, SessionStatus.CANCELLED.value},
    SessionStatus.IN_PROGRESS.value: {SessionStatus.PENDING_REVIEW.value, SessionStatus.CANCELLED.value},
    SessionStatus.PENDING_REVIEW.value: {SessionStatus.COMPLETED.value, SessionStatus.IN_PROGRESS.value},
    SessionStatus.COMPLETED.value: set(),
    SessionStatus.CANCELLED.value: set(),
}


def assert_transition(current: str, target: str) -> None:
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise ApiError(
            409,
            f"Cannot move counting session from {current} to {target}",
            "invalid_transition",
        )


def get_active_session(db: DbSession, for_update: bool = False):
    """Return the single active (non-archived) session if one exists."""
    query = select(CountingSession).where(
        CountingSession.status.in_(list(ACTIVE_SESSION_STATUSES)),
        CountingSession.archived_at.is_(None),
    )
    if for_update:
        query = query.with_for_update()
    return db.execute(query).scalars().first()


def require_session_status(session_row: CountingSession, *statuses: SessionStatus) -> None:
    if session_row.status not in {s.value for s in statuses}:
        raise ApiError(
            409,
            f"Counting session must be in state(s) {[s.value for s in statuses]}, "
            f"currently {session_row.status}",
            "invalid_state",
        )


def latest_completed_session(
    db: DbSession, medicine_id: int, stock_audit_id: Optional[int] = None
) -> Optional[CountingSession]:
    """The session whose completed count defines the current physical stock.

    Only COMPLETED, non-archived sessions contribute, and only the MOST RECENT
    completed session for the medicine counts: re-counting a medicine supersedes
    the earlier count instead of adding to it. Cancelled/draft/pending sessions
    never contribute.
    """
    session_query = select(CountingSession).where(
        CountingSession.active_medicine_id == medicine_id,
        CountingSession.status == SessionStatus.COMPLETED.value,
        CountingSession.archived_at.is_(None),
    )
    if stock_audit_id is not None:
        session_query = session_query.where(CountingSession.stock_audit_id == stock_audit_id)
    return db.execute(
        session_query.order_by(
            CountingSession.completed_at.desc().nullslast(),
            CountingSession.id.desc(),
        ).limit(1)
    ).scalars().first()


def count_operator_for_session(db: DbSession, session_row: CountingSession) -> Optional[int]:
    """User id of the operator who recorded the count of a session.

    The operator is taken from the count events themselves (the latest one),
    so the identity shown on an audit item is the person who actually counted,
    not the person who created or reviewed the session.
    """
    operator_id = db.execute(
        select(CountEvent.operator_id)
        .where(CountEvent.session_id == session_row.id)
        .order_by(CountEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return operator_id


def physical_stock_from_completed_counts(
    db: DbSession, medicine_id: int, stock_audit_id: Optional[int] = None
) -> Optional[Decimal]:
    """Physical stock counted for a medicine (see ``latest_completed_session``)."""
    session_row = latest_completed_session(db, medicine_id, stock_audit_id)
    if session_row is None:
        return None
    total = db.execute(
        select(func.coalesce(func.sum(CountEvent.total_base_units), 0)).where(
            CountEvent.session_id == session_row.id
        )
    ).scalar_one()
    return Decimal(total)


def classify_variance(physical: Decimal, expected: Decimal) -> VarianceClassification:
    diff = physical - expected
    if diff == 0:
        return VarianceClassification.MATCH
    return VarianceClassification.SHORTAGE if diff < 0 else VarianceClassification.SURPLUS


def sync_audit_item_for_medicine(db: DbSession, session_row: CountingSession) -> None:
    """After a session completes, refresh the linked audit item with physical stock and variance."""
    if session_row.stock_audit_id is None or session_row.active_medicine_id is None:
        return
    audit = db.get(StockAudit, session_row.stock_audit_id)
    if audit is None or audit.status != StockAuditStatus.OPEN.value:
        return
    # Flush pending state changes (e.g. session -> COMPLETED) so aggregate
    # queries in this transaction see them.
    db.flush()
    item = db.execute(
        select(StockAuditItem).where(
            StockAuditItem.stock_audit_id == audit.id,
            StockAuditItem.medicine_id == session_row.active_medicine_id,
        )
    ).scalar_one_or_none()
    if item is None:
        item = StockAuditItem(
            stock_audit_id=audit.id,
            medicine_id=session_row.active_medicine_id,
        )
        db.add(item)
    item.expected_stock = expected_stock(db, session_row.active_medicine_id)
    physical = physical_stock_from_completed_counts(
        db, session_row.active_medicine_id, stock_audit_id=audit.id
    )
    if physical is None:
        # The completed session is not visible in the aggregate (e.g. archived):
        # keep the item uncounted instead of storing a false shortage.
        item.physical_stock = None
        item.variance = None
        item.classification = VarianceClassification.UNCOUNTED.value
    else:
        item.physical_stock = physical
        item.variance = physical - item.expected_stock
        item.classification = classify_variance(physical, item.expected_stock).value
    # Identity: `counted_by` is the person who counted (operator of the count
    # event that produced the physical stock); `reviewed_by` is the person who
    # approved the session. They are different people by policy, so the
    # counter shown on an audit item is never the reviewer.
    counted_session = latest_completed_session(db, session_row.active_medicine_id, stock_audit_id=audit.id)
    operator_id = count_operator_for_session(db, counted_session) if counted_session else None
    item.counted_by = operator_id if operator_id is not None else session_row.created_by
    item.reviewed_by = session_row.reviewed_by
    item.counted_at = utcnow()
    db.add(item)


def authorize_reviewer(db: DbSession, session_row: CountingSession, reviewer: User) -> None:
    """Review authorisation policy, in one place.

    A counting session has to be reviewed by somebody who is independent of the
    counting work that produced it. The rules are:

    1. the session must be in ``PENDING_REVIEW`` (checked by the caller via
       ``require_session_status``);
    2. the reviewer must hold the ``count:review`` permission (enforced by the
       router dependency ``require_permission("count:review")``);
    3. the creator of the session may not review it;
    4. a user who recorded a count event on the session may not review it.

    Rules 3 and 4 are two faces of the same separation-of-duties requirement:
    a reviewer must not be the person who did the counting.

    Rule 4 is currently unreachable through the HTTP API, because recording a
    count event is restricted to the session creator (``routers/counting.py``).
    It is kept deliberately as defence in depth: the policy stays correct if
    counting is ever widened to other operators, and it is covered directly by
    the test-suite so it cannot rot unnoticed.
    """
    if reviewer.id == session_row.created_by:
        raise ApiError(403, "A counting session cannot be reviewed by its creator", "self_review")
    already_reviewed = db.execute(
        select(func.count())
        .select_from(CountEvent)
        .where(
            CountEvent.session_id == session_row.id,
            CountEvent.operator_id == reviewer.id,
        )
    ).scalar_one()
    if already_reviewed:
        raise ApiError(403, "A counting session cannot be reviewed by one of its operators", "self_review")


def complete_review(
    db: DbSession,
    session_row: CountingSession,
    decision: ReviewDecision,
    reviewer: User,
    notes: Optional[str],
) -> None:
    require_session_status(session_row, SessionStatus.PENDING_REVIEW)
    authorize_reviewer(db, session_row, reviewer)

    session_row.review_count = (session_row.review_count or 0) + 1
    session_row.reviewed_by = reviewer.id
    session_row.review_decision = decision.value
    session_row.review_notes = notes

    if decision == ReviewDecision.APPROVED:
        assert_transition(session_row.status, SessionStatus.COMPLETED.value)
        session_row.status = SessionStatus.COMPLETED.value
        session_row.completed_at = utcnow()
        sync_audit_item_for_medicine(db, session_row)
    else:
        assert_transition(session_row.status, SessionStatus.IN_PROGRESS.value)
        session_row.status = SessionStatus.IN_PROGRESS.value
    db.add(session_row)
