"""Permission catalogue and default role → permission mappings."""
from app.models.users import Permission, Role, User

PERMISSIONS: dict[str, str] = {
    "user:manage": "Manage users and role assignments",
    "medicine:view": "Search and view medicine records",
    "medicine:manage": "Create, edit and deactivate medicines",
    "barcode:manage": "Register and manage medicine barcodes",
    "package:manage": "Configure medicine package conversions",
    "inventory:view": "View inventory transactions",
    "inventory:transact": "Record inventory transactions",
    "count:perform": "Perform static counting sessions",
    "count:review": "Review and approve/reject counting sessions",
    "count:manage": "Archive, cancel or remove accidental/duplicate counting sessions",
    "audit:view": "View physical stock audits and variances",
    "audit:initiate": "Initiate physical stock audits",
    "audit:manage": "Manage and complete physical stock audits",
    "report:view": "View stock and variance reports",
    "auditlog:view": "View the system audit log",
    "settings:manage": "Configure system settings",
}

ALL_PERMISSIONS = sorted(PERMISSIONS.keys())

DEFAULT_ROLES: dict[str, list[str]] = {
    "administrator": ALL_PERMISSIONS,
    "pharmacist": [
        "medicine:view",
        "inventory:view",
        "inventory:transact",
        "count:perform",
        "count:review",
        "count:manage",
        "audit:view",
        "audit:initiate",
        "audit:manage",
        "report:view",
    ],
    "stock_counter": [
        "medicine:view",
        "count:perform",
    ],
}


def user_permission_codes(user: User) -> set[str]:
    codes: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            codes.add(perm.code)
    return codes


def user_has_permission(user: User, code: str) -> bool:
    return code in user_permission_codes(user)


def ensure_roles_and_permissions(db) -> None:
    """Idempotently create permission and role rows."""
    existing = {p.code for p in db.query(Permission).all()}
    for code, description in PERMISSIONS.items():
        if code not in existing:
            db.add(Permission(code=code, description=description))
        else:
            perm = db.query(Permission).filter(Permission.code == code).one()
            if perm.description != description:
                perm.description = description
    db.flush()

    perms_by_code = {p.code: p for p in db.query(Permission).all()}
    existing_roles = {r.name: r for r in db.query(Role).all()}
    for role_name, codes in DEFAULT_ROLES.items():
        role = existing_roles.get(role_name)
        if role is None:
            role = Role(name=role_name, description=f"Default role: {role_name}")
            db.add(role)
            db.flush()
        current = {p.code for p in role.permissions}
        for code in codes:
            if code not in current:
                role.permissions.append(perms_by_code[code])
    db.commit()
