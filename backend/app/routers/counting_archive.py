"""Counting-session archiving (soft delete) with audit trail.

Cancelling a session keeps it in the database but it should not clutter the
normal session list. Archiving hides it from the default list while preserving
every row, count event and audit trail entry. Records are never hard-deleted.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.database import get_db, utcnow
from app.deps import require_permission
from app.errors import ApiError
from app.models.counting import CountingSession, SessionStatus
from app.models.users import User
from app.routers.counting import _session_out
from app.schemas.counting import ArchiveSessionRequest, CountingSessionOut

router = APIRouter(prefix="/api/counting", tags=["counting"])


def _get_locked_session(db: DbSession, session_id: int) -> CountingSession:
    row = db.execute(
        select(CountingSession).where(CountingSession.id == session_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "Counting session not found", "not_found")
    return row


@router.post("/sessions/{session_id}/archive", response_model=CountingSessionOut)
def archive_session(
    session_id: int,
    payload: ArchiveSessionRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:manage")),
):
    """Soft-archive a cancelled (or empty draft) session. Never deletes data."""
    row = _get_locked_session(db, session_id)
    if row.archived_at is not None:
        raise ApiError(409, "Session is already archived", "already_archived")
    if row.status == SessionStatus.COMPLETED.value:
        raise ApiError(
            409,
            "Completed sessions are part of the stock history and cannot be archived",
            "completed_session",
        )
    if row.status not in (SessionStatus.CANCELLED.value, SessionStatus.DRAFT.value):
        raise ApiError(
            409,
            "Only cancelled or draft sessions may be archived — cancel the session first",
            "invalid_state",
        )

    row.archived_at = utcnow()
    row.archived_by = user.id
    row.archive_reason = payload.reason
    db.add(row)
    log_action(
        db,
        action="counting.session_archive",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        before={"archived_at": None},
        after={"status": row.status, "reason": payload.reason, "archived_at": row.archived_at.isoformat()},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.post("/sessions/{session_id}/unarchive", response_model=CountingSessionOut)
def unarchive_session(
    session_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:manage")),
):
    """Restore an archived session to the normal list."""
    row = _get_locked_session(db, session_id)
    if row.archived_at is None:
        raise ApiError(409, "Session is not archived", "not_archived")
    if row.status in (
        SessionStatus.DRAFT.value,
        SessionStatus.IN_PROGRESS.value,
        SessionStatus.PENDING_REVIEW.value,
    ):
        raise ApiError(
            409,
            "Only cancelled sessions can be restored — an active session would break the "
            "one-active-session rule",
            "invalid_state",
        )

    before = {"archived_at": row.archived_at.isoformat(), "reason": row.archive_reason}
    row.archived_at = None
    row.archived_by = None
    row.archive_reason = None
    db.add(row)
    log_action(
        db,
        action="counting.session_unarchive",
        entity_type="counting_session",
        entity_id=row.id,
        actor=user,
        before=before,
        after={"archived_at": None},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _session_out(db, row)
