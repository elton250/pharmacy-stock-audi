"""Password hashing and server-side session management."""
import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.database import utcnow
from app.models.users import Session, User

_hasher = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: DbSession,
    user: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[Session, str, str]:
    """Create a server-side session. Returns (session_row, raw_token, csrf_token)."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = utcnow()
    row = Session(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        csrf_token=csrf_token,
        created_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=now,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:400] or None,
    )
    db.add(row)
    db.flush()
    return row, raw_token, csrf_token


def get_session_by_token(db: DbSession, raw_token: str) -> Session | None:
    return db.execute(select(Session).where(Session.token_hash == _hash_token(raw_token))).scalar_one_or_none()


def revoke_session(db: DbSession, session_row: Session) -> None:
    if session_row.revoked_at is None:
        session_row.revoked_at = utcnow()
        db.add(session_row)


def touch_session(db: DbSession, session_row: Session) -> None:
    session_row.last_seen_at = utcnow()
    db.add(session_row)
