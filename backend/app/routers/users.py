"""User management (administrators only)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.database import get_db
from app.deps import require_permission
from app.errors import ApiError
from app.models.users import Role, User
from app.schemas.common import Page
from app.schemas.users import UserCreate, UserOut, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


def _load_roles(db: DbSession, names: list[str]) -> list[Role]:
    roles = db.execute(select(Role).where(Role.name.in_(names))).scalars().all()
    found = {r.name for r in roles}
    missing = set(names) - found
    if missing:
        raise ApiError(422, f"Unknown roles: {', '.join(sorted(missing))}", "unknown_roles")
    return list(roles)


def _user_snapshot(user: User) -> dict:
    return {
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "is_active": user.is_active,
        "roles": sorted(r.name for r in user.roles),
    }


@router.get("", response_model=Page[UserOut])
def list_users(
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("user:manage")),
    search: Optional[str] = Query(default=None, max_length=120),
    is_active: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    query = select(User)
    count_query = select(func.count()).select_from(User)
    if search:
        pattern = f"%{search}%"
        cond = or_(User.username.ilike(pattern), User.full_name.ilike(pattern))
        query = query.where(cond)
        count_query = count_query.where(cond)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)
    total = db.execute(count_query).scalar_one()
    rows = (
        db.execute(query.order_by(User.username).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return Page[UserOut](
        items=[UserOut.model_validate(u) for u in rows], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    actor: User = Depends(require_permission("user:manage")),
):
    exists = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if exists is not None:
        raise ApiError(409, "Username already exists", "username_taken")
    roles = _load_roles(db, payload.role_names) if payload.role_names else []
    user = User(
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        is_active=True,
        must_change_password=payload.must_change_password,
        created_by=actor.id,
    )
    user.roles.extend(roles)
    db.add(user)
    db.flush()
    log_action(
        db,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        actor=actor,
        after=_user_snapshot(user),
        request=request,
    )
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("user:manage")),
):
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "User not found", "not_found")
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    actor: User = Depends(require_permission("user:manage")),
):
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "User not found", "not_found")
    before = _user_snapshot(user)

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        user.email = payload.email
    if payload.is_active is not None:
        if user.id == actor.id and payload.is_active is False:
            raise ApiError(422, "You cannot deactivate your own account", "self_deactivate")
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    if payload.role_names is not None:
        if user.id == actor.id and "administrator" not in payload.role_names:
            raise ApiError(422, "You cannot remove your own administrator role", "self_demote")
        user.roles = _load_roles(db, payload.role_names)
    user.updated_by = actor.id
    db.add(user)
    db.flush()
    log_action(
        db,
        action="user.update",
        entity_type="user",
        entity_id=user.id,
        actor=actor,
        before=before,
        after=_user_snapshot(user),
        request=request,
    )
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/roles/all")
def list_roles(db: DbSession = Depends(get_db), _: User = Depends(require_permission("user:manage"))):
    roles = db.execute(select(Role).order_by(Role.name)).scalars().all()
    return [
        {"id": r.id, "name": r.name, "description": r.description, "permissions": sorted(p.code for p in r.permissions)}
        for r in roles
    ]
