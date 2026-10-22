"""Management CLI for the Pharmacy Stock Auditing System.

    python -m app.cli create-admin \
        [--username admin] [--email admin@pharmacy.local] \
        [--password ... | PHARMACY_ADMIN_PASSWORD | interactive prompt] \
        [--full-name "Site Admin"]

    python -m app.cli reset-admin-password \
        [--username admin] [--password ... | PHARMACY_ADMIN_PASSWORD | interactive prompt] [--yes]

    python -m app.cli prune-vision-captures [--dry-run] [--retention-days 30]

Security rules enforced by this command:
- Passwords are hashed with argon2 before storage; plaintext is never written
  to the database, logs, or echoed back after entry.
- The initial administrator can only be created when NO administrator exists.
  An existing administrator is never overwritten, recreated, or modified.
- The created administrator is flagged must_change_password=True, so the API
  forces a password change on first login.
- reset-admin-password updates the existing account in place (never deletes
  or duplicates it), revokes its sessions, and requires a password change at
  next login. It requires explicit confirmation (--yes or interactive YES).
- For production, omit --password and either set PHARMACY_ADMIN_PASSWORD in
  the environment or answer the interactive prompt (input is hidden).
"""
import argparse
import os
import sys

from sqlalchemy import select

from app.audit import log_action
from app.database import SessionLocal, utcnow
from app.models.users import Role, Session, User
from app.permissions import ensure_roles_and_permissions
from app.security import hash_password, verify_password

# Minimum length mirrors the API schema constraint.
MIN_PASSWORD_LENGTH = 8
# Username/email defaults target local development; production deployments
# should override them explicitly or via the interactive prompt.
DEFAULT_USERNAME = "admin"
DEFAULT_EMAIL = "admin@pharmacy.local"
DEFAULT_FULL_NAME = "Administrator"


def _resolve_password(cli_password: str | None) -> str:
    """Resolve the password from --password, PHARMACY_ADMIN_PASSWORD, or a hidden prompt."""
    if cli_password:
        return cli_password
    env_password = os.environ.get("PHARMACY_ADMIN_PASSWORD")
    if env_password:
        return env_password
    try:
        import getpass

        first = getpass.getpass("Password (hidden input): ")
        second = getpass.getpass("Repeat password: ")
    except (EOFError, KeyboardInterrupt):
        print("\nERROR: aborted", file=sys.stderr)
        sys.exit(1)
    if first != second:
        print("ERROR: passwords do not match", file=sys.stderr)
        sys.exit(1)
    return first


def create_admin(
    username: str = DEFAULT_USERNAME,
    password: str | None = None,
    full_name: str = DEFAULT_FULL_NAME,
    email: str | None = DEFAULT_EMAIL,
) -> None:
    resolved_password = _resolve_password(password)
    if len(resolved_password) < MIN_PASSWORD_LENGTH:
        print(f"ERROR: password must be at least {MIN_PASSWORD_LENGTH} characters", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        ensure_roles_and_permissions(db)

        # Never recreate or overwrite an existing administrator.
        admin_role = db.execute(select(Role).where(Role.name == "administrator")).scalar_one()
        existing_admin = (
            db.execute(
                select(User)
                .join(User.roles)
                .where(Role.name == "administrator")
                .limit(1)
            )
            .scalars()
            .first()
        )
        if existing_admin is not None:
            print(
                "ERROR: an administrator already exists "
                f"(user id={existing_admin.id}, username='{existing_admin.username}'). "
                "The initial administrator can only be created once; refusing to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)

        existing_username = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing_username is not None:
            print(f"ERROR: user '{username}' already exists", file=sys.stderr)
            sys.exit(1)

        password_hash = hash_password(resolved_password)
        # Sanity check: the hash must verify and must not contain the plaintext.
        if not verify_password(password_hash, resolved_password) or resolved_password in password_hash:
            print("ERROR: password hashing self-check failed", file=sys.stderr)
            sys.exit(1)

        user = User(
            username=username,
            full_name=full_name or username,
            email=email or None,
            password_hash=password_hash,
            is_active=True,
            must_change_password=True,  # forced change on first login
        )
        user.roles.append(admin_role)
        db.add(user)
        db.commit()
        print(f"Administrator '{username}' created (email: {email or 'n/a'}).")
        print("Password stored as an argon2 hash. A password change is required on first login.")
    finally:
        db.close()


def reset_admin_password(username: str = DEFAULT_USERNAME, password: str | None = None, assume_yes: bool = False) -> None:
    """Secure recovery for an existing administrator's password.

    Preserves the account (updates it in place, never recreates or deletes),
    stores only an argon2 hash, revokes all of the account's sessions, and
    flags the account so a password change is required at next login.
    Requires explicit confirmation (--yes or an interactive YES) because CLI
    access is the authorization boundary.
    """
    resolved_password = _resolve_password(password)
    if len(resolved_password) < MIN_PASSWORD_LENGTH:
        print(f"ERROR: password must be at least {MIN_PASSWORD_LENGTH} characters", file=sys.stderr)
        sys.exit(1)

    if not assume_yes:
        if not sys.stdin.isatty():
            print(
                "ERROR: refusing to reset a password non-interactively without --yes",
                file=sys.stderr,
            )
            sys.exit(1)
        answer = input(f"Reset the password for administrator '{username}'? Type YES to confirm: ")
        if answer.strip() != "YES":
            print("ERROR: confirmation not given; aborting", file=sys.stderr)
            sys.exit(1)

    db = SessionLocal()
    try:
        user = (
            db.execute(
                select(User).join(User.roles).where(Role.name == "administrator", User.username == username)
            )
            .scalars()
            .first()
        )
        if user is None:
            print(
                f"ERROR: no administrator with username '{username}' exists; nothing was changed.",
                file=sys.stderr,
            )
            sys.exit(1)

        password_hash = hash_password(resolved_password)
        if not verify_password(password_hash, resolved_password) or resolved_password in password_hash:
            print("ERROR: password hashing self-check failed", file=sys.stderr)
            sys.exit(1)

        user.password_hash = password_hash
        user.must_change_password = True

        # Revoke every active session for the account.
        now = utcnow()
        revoked = 0
        for session_row in db.execute(
            select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))
        ).scalars():
            session_row.revoked_at = now
            db.add(session_row)
            revoked += 1

        log_action(
            db,
            action="user.password_reset_cli",
            entity_type="user",
            entity_id=user.id,
            actor_username="cli",
            after={"target_username": user.username, "sessions_revoked": revoked, "must_change_password": True},
        )
        db.commit()
        print(f"Password reset for administrator '{username}' (user id={user.id}).")
        print(f"Revoked {revoked} active session(s). A password change is required at next login.")
    finally:
        db.close()


def prune_vision_captures(retention_days: int | None = None, dry_run: bool = False) -> None:
    """Delete captured images that are older than the retention window.

    Captured camera frames are personal data and must not be kept forever:
    the default retention is 30 days. Only *pending* captures can still be
    expired — a confirmed or rejected capture is already closed and its image
    was deleted at that point.
    """
    from app.services.vision import prune_expired_captures

    db = SessionLocal()
    try:
        result = prune_expired_captures(db, retention_days=retention_days, dry_run=dry_run)
    finally:
        db.close()

    prefix = "would delete" if dry_run else "deleted"
    print(
        f"{prefix} {result['images_deleted']} image(s) of {result['expired_captures']} "
        f"capture(s) older than {result['retention_days']} days (cutoff {result['cutoff']})."
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_admin = sub.add_parser(
        "create-admin",
        help="Create the initial administrator account (only when no administrator exists)",
        epilog=(
            "Password sources (in order): --password flag, PHARMACY_ADMIN_PASSWORD "
            "environment variable, hidden interactive prompt. "
            "For local development the suggested credentials are "
            "admin@pharmacy.local / Admin@Pharma2026! (pass the password explicitly)."
        ),
    )
    p_admin.add_argument("--username", default=DEFAULT_USERNAME)
    p_admin.add_argument("--email", default=DEFAULT_EMAIL)
    p_admin.add_argument(
        "--password",
        default=None,
        help="Admin password (prefer PHARMACY_ADMIN_PASSWORD env or the hidden prompt in production)",
    )
    p_admin.add_argument("--full-name", default=DEFAULT_FULL_NAME)

    p_reset = sub.add_parser(
        "reset-admin-password",
        help="Reset an existing administrator's password (preserves the account; forces change at next login)",
        epilog=(
            "Recovery command for a lost administrator password. Updates the existing account "
            "in place (never recreates or deletes it), revokes all of its sessions, and requires "
            "a password change at next login. Confirmation is required: pass --yes for "
            "non-interactive use, or type YES at the prompt. Password sources (in order): "
            "--password flag, PHARMACY_ADMIN_PASSWORD environment variable, hidden prompt."
        ),
    )
    p_reset.add_argument("--username", default=DEFAULT_USERNAME, help="Administrator username to reset")
    p_reset.add_argument("--password", default=None, help="New password (prefer env var or hidden prompt)")
    p_reset.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the reset non-interactively (required when stdin is not a TTY)",
    )

    p_prune = sub.add_parser(
        "prune-vision-captures",
        help="Delete captured camera images that are older than the retention window",
        epilog=(
            "Captured frames are deleted after the retention window (default 30 days). "
            "Use --dry-run to see what would be deleted without touching anything."
        ),
    )
    p_prune.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Retention window in days (default: the configured vision_retention_days)",
    )
    p_prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would be deleted, change nothing",
    )

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(
            username=args.username,
            password=args.password,
            full_name=args.full_name,
            email=args.email,
        )
    elif args.command == "reset-admin-password":
        reset_admin_password(
            username=args.username,
            password=args.password,
            assume_yes=args.yes,
        )
    elif args.command == "prune-vision-captures":
        prune_vision_captures(
            retention_days=args.retention_days,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
