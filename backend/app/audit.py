"""Audit-log helper. Logs are append-only and never contain secrets."""
import json
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session as DbSession

from app.models.logs import AuditLog
from app.models.users import User

_SENSITIVE_KEYS = {"password", "password_hash", "token", "token_hash", "csrf_token", "secret"}


def _sanitize(data: Any) -> Any:
    if data is None:
        return None
    if isinstance(data, dict):
        return {
            k: "***" if k.lower() in _SENSITIVE_KEYS else _sanitize(v)
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_sanitize(v) for v in data]
    if hasattr(data, "isoformat"):
        return data.isoformat()
    if isinstance(data, (str, int, float, bool)) or data is None:
        return data
    return json.loads(json.dumps(data, default=str))


def log_action(
    db: DbSession,
    *,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    actor: Optional[User] = None,
    actor_username: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    request: Optional[Request] = None,
) -> AuditLog:
    ip = None
    ua = None
    if request is not None:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
    row = AuditLog(
        actor_id=actor.id if actor is not None else None,
        actor_username=actor.username if actor is not None else actor_username,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_data=_sanitize(before),
        after_data=_sanitize(after),
        ip_address=ip,
        user_agent=(ua or "")[:400] or None,
    )
    db.add(row)
    return row
