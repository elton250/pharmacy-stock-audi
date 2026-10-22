"""FastAPI dependencies: current user, CSRF protection and permission checks."""
from typing import Callable, Optional

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.database import get_db, utcnow
from app.errors import ApiError
from app.models.users import User
from app.permissions import user_permission_codes
from app.security import get_session_by_token, touch_session

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths a user with must_change_password=True may still access.
_PASSWORD_CHANGE_EXEMPT_PATHS = {
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/change-password",
}


def get_client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def get_current_user(request: Request, db: DbSession = Depends(get_db)) -> User:
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise ApiError(401, "Not authenticated", "not_authenticated")
    session_row = get_session_by_token(db, raw_token)
    if session_row is None or session_row.revoked_at is not None:
        raise ApiError(401, "Session is invalid or has been revoked", "invalid_session")
    if session_row.expires_at <= utcnow():
        raise ApiError(401, "Session has expired", "session_expired")
    user = db.get(User, session_row.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "User account is inactive", "inactive_user")

    # CSRF: for mutating requests require the session-bound CSRF token header.
    if request.method in _MUTATING_METHODS:
        header_token = request.headers.get("X-CSRF-Token", "")
        cookie_token = request.cookies.get(settings.csrf_cookie_name, "")
        if not header_token or header_token != session_row.csrf_token:
            raise ApiError(403, "CSRF token missing or invalid", "csrf_failed")
        # Cookie must match too when present (double-submit sanity check).
        if cookie_token and cookie_token != session_row.csrf_token:
            raise ApiError(403, "CSRF cookie does not match session", "csrf_failed")

    # Forced password change: block everything except me/logout/change-password.
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_EXEMPT_PATHS:
        raise ApiError(
            403,
            "You must change your password before continuing",
            "password_change_required",
        )

    touch_session(db, session_row)
    db.commit()
    request.state.session_id = session_row.id
    request.state.user = user
    return user


def require_permission(code: str) -> Callable:
    def checker(user: User = Depends(get_current_user)) -> User:
        if code not in user_permission_codes(user):
            raise ApiError(403, f"Missing required permission: {code}", "forbidden")
        return user

    return checker


def require_any_permission(*codes: str) -> Callable:
    def checker(user: User = Depends(get_current_user)) -> User:
        if not user_permission_codes(user).intersection(codes):
            raise ApiError(403, f"Missing required permission: one of {', '.join(codes)}", "forbidden")
        return user

    return checker
