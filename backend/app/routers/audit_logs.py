"""Audit-log viewer (restricted)."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.database import get_db
from app.deps import require_permission
from app.models.logs import AuditLog
from app.models.users import User
from app.schemas.common import Page

router = APIRouter(prefix="/api/audit-logs", tags=["audit-logs"])


class AuditLogModel(BaseModel):
    id: int
    actor_id: Optional[int] = None
    actor_username: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    before_data: Optional[dict] = None
    after_data: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=Page[AuditLogModel])
def list_audit_logs(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("auditlog:view")),
    action: Optional[str] = Query(default=None, max_length=80),
    entity_type: Optional[str] = Query(default=None, max_length=80),
    entity_id: Optional[str] = Query(default=None, max_length=80),
    actor_username: Optional[str] = Query(default=None, max_length=80),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
        count_query = count_query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
        count_query = count_query.where(AuditLog.entity_id == entity_id)
    if actor_username:
        query = query.where(AuditLog.actor_username == actor_username)
        count_query = count_query.where(AuditLog.actor_username == actor_username)
    if since:
        query = query.where(AuditLog.created_at >= since)
        count_query = count_query.where(AuditLog.created_at >= since)
    if until:
        query = query.where(AuditLog.created_at <= until)
        count_query = count_query.where(AuditLog.created_at <= until)
    total = db.execute(count_query).scalar_one()
    rows = (
        db.execute(query.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return Page[AuditLogModel](
        items=[AuditLogModel.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
