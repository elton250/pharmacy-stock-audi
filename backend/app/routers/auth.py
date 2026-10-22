"""Authentication: initial setup, login, logout, current user."""
import threading
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.config import get_settings
from app.database import get_db, utcnow
from app.deps import get_client_ip, get_current_user
from app.errors import ApiError
from app.models.users import LoginAttempt, Role, Session, User
from app.permissions import ensure_roles_and_permissions
from app.schemas.users import ChangePasswordRequest, LoginRequest, SetupRequest, TokenResponse, UserOut
from app.security import create_session, hash_password, revoke_session, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Login rate limiting (per-process sliding window; documented Phase 1 limit)
# ---------------------------------------------------------------------------
_rate_lock = threading.Lock()
_failures: dict[str, deque] = defaultdict(deque)


def _rate_limit_key(username: str, ip: str | None) -> str:
    return f"{username.lower()}|{ip or '-'}"


def _is_rate_limited(username: str, ip: str | None) -> bool:
    settings = get_settings()
    now = utcnow()
    window = timedelta(minutes=settings.login_window_minutes)
    key = _rate_limit_key(username, ip)
    with _rate_lock:
        q = _failures[key]
        while q and now - q[0] > window:
            q.popleft()
        return len(q) >= settings.login_max_failures


def _record_failure(username: str, ip: str | None) -> None:
    key = _rate_limit_key(username, ip)
    with _rate_lock:
        _failures[key].append(utcnow())


def _clear_failures(username: str, ip: str | None) -> None:
    key = _rate_limit_key(username, ip)
    with _rate_lock:
        _failures[key].clear()


def reset_rate_limiter() -> None:
    """Used by tests to avoid cross-test leakage."""
    with _rate_lock:
        _failures.clear()


def _set_auth_cookies(response: Response, raw_token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        max_age=max_age,
        httponly=True,
        samesite=settings.cookie_same_site,
        secure=settings.cookie_secure,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        httponly=False,  # readable by frontend JS to send in X-CSRF-Token
        samesite=settings.cookie_same_site,
        secure=settings.cookie_secure,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


@router.get("/setup-available")
def setup_available(db: DbSession = Depends(get_db)) -> dict:
    count = db.execute(select(func.count()).select_from(User)).scalar_one()
    return {"available": count == 0}


@router.post("/setup", response_model=TokenResponse, status_code=201)
def setup(payload: SetupRequest, request: Request, response: Response, db: DbSession = Depends(get_db)):
    """Protected initial administrator creation: only allowed when no users exist."""
    count = db.execute(select(func.count()).select_from(User)).scalar_one()
    if count > 0:
        raise ApiError(409, "Setup is disabled: users already exist", "setup_disabled")

    ensure_roles_and_permissions(db)
    admin_role = db.execute(select(Role).where(Role.name == "administrator")).scalar_one()
    user = User(
        username=payload.username,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    user.roles.append(admin_role)
    db.add(user)
    db.flush()
    log_action(
        db,
        action="user.setup_admin",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        after={"username": user.username, "roles": ["administrator"]},
        request=request,
    )
    session_row, raw_token, csrf_token = create_session(
        db, user, ip_address=get_client_ip(request), user_agent=request.headers.get("user-agent")
    )
    db.commit()
    _set_auth_cookies(response, raw_token, csrf_token)
    return TokenResponse(detail="Initial administrator created", user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: DbSession = Depends(get_db)):
    ip = get_client_ip(request)
    if _is_rate_limited(payload.username, ip):
        raise ApiError(429, "Too many failed login attempts. Try again later.", "rate_limited")

    user = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    success = user is not None and user.is_active and verify_password(user.password_hash, payload.password)

    db.add(
        LoginAttempt(
            username=payload.username, ip_address=ip, success=bool(success), created_at=utcnow()
        )
    )
    if not success:
        log_action(
            db,
            action="auth.login_failed",
            entity_type="user",
            entity_id=user.id if user else None,
            actor_username=payload.username,
            after={"reason": "invalid_credentials" if user else "unknown_user"},
            request=request,
        )
        db.commit()
        _record_failure(payload.username, ip)
        # Uniform message: do not reveal whether the user exists.
        raise ApiError(401, "Invalid username or password", "invalid_credentials")

    _clear_failures(payload.username, ip)
    session_row, raw_token, csrf_token = create_session(
        db, user, ip_address=ip, user_agent=request.headers.get("user-agent")
    )
    log_action(
        db,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        request=request,
    )
    db.commit()
    _set_auth_cookies(response, raw_token, csrf_token)
    return TokenResponse(detail="Logged in", user=UserOut.model_validate(user))


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    from app.security import get_session_by_token

    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        session_row = get_session_by_token(db, raw_token)
        if session_row is not None and session_row.revoked_at is None:
            revoke_session(db, session_row)
            log_action(
                db,
                action="auth.logout",
                entity_type="user",
                entity_id=session_row.user_id,
                request=request,
            )
            db.commit()
    _clear_auth_cookies(response)
    return {"detail": "Logged out"}


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Change the current user's password. Clears must_change_password and revokes other sessions."""
    if not verify_password(user.password_hash, payload.current_password):
        log_action(
            db,
            action="auth.password_change_failed",
            entity_type="user",
            entity_id=user.id,
            actor=user,
            after={"reason": "invalid_current_password"},
            request=request,
        )
        db.commit()
        raise ApiError(401, "Current password is incorrect", "invalid_credentials")

    if payload.new_password == user.username:
        raise ApiError(422, "New password must not match the username", "weak_password")

    old_flag = user.must_change_password
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False

    # Revoke every other active session for this user (keep the current one).
    current_session_id = getattr(request.state, "session_id", None)
    others = db.execute(
        select(Session).where(
            Session.user_id == user.id,
            Session.revoked_at.is_(None),
            Session.id != current_session_id,
        )
    ).scalars().all()
    for row in others:
        revoke_session(db, row)

    log_action(
        db,
        action="auth.password_changed",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        after={
            "must_change_password_cleared": old_flag,
            "other_sessions_revoked": len(others),
        },
        request=request,
    )
    db.commit()
    db.refresh(user)
    return TokenResponse(detail="Password changed", user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)
