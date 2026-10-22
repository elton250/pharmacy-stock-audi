"""Human-readable unique code generation for sessions and audits."""
import secrets
import string
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

_ALPHABET = string.ascii_uppercase + string.digits


def _suffix(length: int = 6) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def generate_unique_code(db: DbSession, model, prefix: str) -> str:
    """Generate e.g. CS-20261018-A1B2C3, retrying on rare collisions."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for _ in range(10):
        code = f"{prefix}-{today}-{_suffix()}"
        existing = db.query(model).filter(model.code == code).first()
        if existing is None:
            return code
    raise RuntimeError("Unable to generate a unique code")
