"""Tests for the secure CLI admin bootstrap and the forced password-change flow."""
import pytest
from sqlalchemy import select

from app.cli import create_admin
from app.database import SessionLocal
from app.models.users import Role, User
from app.security import verify_password


@pytest.fixture()
def clean_db(raw_client, db):
    """raw_client truncates all tables at setup; db is a ready session."""
    return db


class TestCliCreateAdmin:
    def test_creates_admin_with_argon2_hash_and_forced_change(self, clean_db):
        create_admin(username="rootadmin", password="Bootstrap-Pw-1!", email="admin@pharmacy.local")

        user = clean_db.execute(select(User).where(User.username == "rootadmin")).scalar_one()
        assert user.email == "admin@pharmacy.local"
        assert user.is_active is True
        assert user.must_change_password is True
        # Hashed, never plaintext.
        assert user.password_hash.startswith("$argon2")
        assert "Bootstrap-Pw-1!" not in user.password_hash
        assert verify_password(user.password_hash, "Bootstrap-Pw-1!")
        assert any(r.name == "administrator" for r in user.roles)

    def test_refuses_when_administrator_exists(self, clean_db, capsys):
        create_admin(username="first-admin", password="FirstAdmin-Pw1")

        with pytest.raises(SystemExit) as exc:
            create_admin(username="second-admin", password="SecondAdmin-Pw1")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "already exists" in err

        # Second call must not create or modify anything.
        users = clean_db.execute(select(User).order_by(User.id)).scalars().all()
        assert [u.username for u in users] == ["first-admin"]
        assert users[0].must_change_password is True

    def test_password_from_env_variable(self, clean_db, monkeypatch):
        monkeypatch.setenv("PHARMACY_ADMIN_PASSWORD", "EnvSecret-Pw99")
        create_admin(username="env-admin")

        user = clean_db.execute(select(User).where(User.username == "env-admin")).scalar_one()
        assert verify_password(user.password_hash, "EnvSecret-Pw99")
        assert "EnvSecret-Pw99" not in user.password_hash

    def test_rejects_short_password(self, clean_db):
        with pytest.raises(SystemExit) as exc:
            create_admin(username="short-pw", password="short")
        assert exc.value.code == 1
        assert clean_db.execute(select(User)).scalars().all() == []


class TestForcedPasswordChange:
    def _bootstrap_flagged_admin(self, api):
        """Create admin via CLI (flagged), then log in through the API."""
        create_admin(username="cliadmin", password="Bootstrap-Pw-1!", email="admin@pharmacy.local")
        resp = api.post("/api/auth/login", json={"username": "cliadmin", "password": "Bootstrap-Pw-1!"})
        assert resp.status_code == 200, resp.text
        return resp.json()["user"]

    def test_login_returns_must_change_flag(self, api):
        user = self._bootstrap_flagged_admin(api)
        assert user["must_change_password"] is True

    def test_application_endpoints_blocked_until_changed(self, api):
        self._bootstrap_flagged_admin(api)

        # Exempt endpoints still work.
        me = api.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["must_change_password"] is True

        # Everything else is blocked with a clear error code.
        resp = api.get("/api/medicines")
        assert resp.status_code == 403
        assert resp.json()["code"] == "password_change_required"

        resp = api.post(
            "/api/inventory/transactions",
            json={"medicine_id": 1, "transaction_type": "RECEIVED", "quantity": 1},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "password_change_required"

    def test_wrong_current_password_rejected(self, api):
        self._bootstrap_flagged_admin(api)
        resp = api.post(
            "/api/auth/change-password",
            json={"current_password": "wrong-password", "new_password": "NewSecure-Pw1"},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "invalid_credentials"

    def test_new_password_must_differ_from_current(self, api):
        self._bootstrap_flagged_admin(api)
        resp = api.post(
            "/api/auth/change-password",
            json={"current_password": "Bootstrap-Pw-1!", "new_password": "Bootstrap-Pw-1!"},
        )
        assert resp.status_code == 422

    def test_successful_change_clears_flag_and_blocks_old_password(self, api):
        self._bootstrap_flagged_admin(api)
        resp = api.post(
            "/api/auth/change-password",
            json={"current_password": "Bootstrap-Pw-1!", "new_password": "NewSecure-Pw1"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["must_change_password"] is False

        # Application access restored without re-login (session kept).
        assert api.get("/api/medicines").status_code == 200

        # Old password no longer works; new one does.
        api.post("/api/auth/logout")
        assert api.post(
            "/api/auth/login", json={"username": "cliadmin", "password": "Bootstrap-Pw-1!"}
        ).status_code == 401
        resp = api.post("/api/auth/login", json={"username": "cliadmin", "password": "NewSecure-Pw1"})
        assert resp.status_code == 200
        assert resp.json()["user"]["must_change_password"] is False

    def test_change_revokes_other_sessions(self, raw_client, api):
        from fastapi.testclient import TestClient
        from app.main import app

        self._bootstrap_flagged_admin(api)

        # Second browser/session for the same user.
        other = ApiClientLike(TestClient(app))
        login = other.post("/api/auth/login", json={"username": "cliadmin", "password": "Bootstrap-Pw-1!"})
        assert login.status_code == 200

        # Change password in the first session.
        resp = api.post(
            "/api/auth/change-password",
            json={"current_password": "Bootstrap-Pw-1!", "new_password": "NewSecure-Pw1"},
        )
        assert resp.status_code == 200

        # The other session is dead.
        assert other.get("/api/auth/me").status_code == 401
        # The session that changed the password still works.
        assert api.get("/api/auth/me").status_code == 200


class ApiClientLike:
    """Minimal CSRF-aware client for the two-session test."""

    def __init__(self, client):
        self.client = client

    def _headers(self):
        token = self.client.cookies.get("pharmacy_csrf")
        return {"X-CSRF-Token": token} if token else {}

    def get(self, url, **kwargs):
        return self.client.get(url, **kwargs)

    def post(self, url, json=None, **kwargs):
        kwargs["headers"] = self._headers()
        return self.client.post(url, json=json, **kwargs)


class TestResetAdminPassword:
    def test_reset_preserves_account_sets_flag_and_revokes_sessions(self, raw_client, api):
        from app.cli import reset_admin_password
        from app.security import verify_password as vp

        create_admin(username="recover-admin", password="Original-Pw-99!", email="admin@pharmacy.local")
        # Establish an active session with the original password.
        resp = api.post("/api/auth/login", json={"username": "recover-admin", "password": "Original-Pw-99!"})
        assert resp.status_code == 200, resp.text

        reset_admin_password(username="recover-admin", password="Recovered-Pw-42!", assume_yes=True)

        # Account preserved in place: same id, still administrator.
        user = clean_user("recover-admin")
        assert user.id is not None
        assert any(r.name == "administrator" for r in user.roles)
        assert user.must_change_password is True
        assert vp(user.password_hash, "Recovered-Pw-42!")
        assert "Recovered-Pw-42!" not in user.password_hash

        # Old session revoked.
        assert api.get("/api/auth/me").status_code == 401

        # Login requires the change immediately.
        resp = api.post("/api/auth/login", json={"username": "recover-admin", "password": "Recovered-Pw-42!"})
        assert resp.status_code == 200
        assert resp.json()["user"]["must_change_password"] is True
        assert api.get("/api/medicines").status_code == 403  # blocked until changed

        # Old password is dead.
        api.post("/api/auth/logout")
        resp = api.post("/api/auth/login", json={"username": "recover-admin", "password": "Original-Pw-99!"})
        assert resp.status_code == 401

    def test_reset_refuses_non_administrator(self, clean_db, capsys):
        from app.cli import reset_admin_password

        with pytest.raises(SystemExit) as exc:
            reset_admin_password(username="counter", password="Whatever-Pw-1", assume_yes=True)
        assert exc.value.code == 1
        assert "no administrator" in capsys.readouterr().err

    def test_reset_refuses_unknown_username(self, clean_db, capsys):
        from app.cli import reset_admin_password

        with pytest.raises(SystemExit) as exc:
            reset_admin_password(username="ghost", password="Whatever-Pw-1", assume_yes=True)
        assert exc.value.code == 1
        assert "no administrator" in capsys.readouterr().err

    def test_reset_requires_confirmation_non_interactive(self, clean_db, capsys, monkeypatch):
        from app.cli import reset_admin_password

        create_admin(username="confirm-guard", password="Guard-Pw-123!")
        monkeypatch.setattr("sys.stdin", _NonTtyStdin())
        with pytest.raises(SystemExit) as exc:
            reset_admin_password(username="confirm-guard", password="NewGuard-Pw-1")
        assert exc.value.code == 1
        assert "--yes" in capsys.readouterr().err
        # Password unchanged.
        from app.security import verify_password as vp

        assert vp(clean_user("confirm-guard").password_hash, "Guard-Pw-123!")


class TestLoginFieldContract:
    """Regression for the diagnosed login failure: API expects 'username', not 'email'."""

    def test_login_requires_username_field(self, api):
        resp = api.post("/api/auth/login", json={"email": "admin@pharmacy.local", "password": "x"})
        assert resp.status_code == 422
        locs = [err["loc"] for err in resp.json()["detail"]]
        assert ["body", "username"] in locs

    def test_login_by_email_is_not_supported(self, clean_db, api):
        # A user with an email address still logs in with the username only.
        create_admin(username="email-user", password="EmailUser-Pw1!", email="email-user@pharmacy.local")
        api.post("/api/auth/logout")
        # Passing the email as the username value fails (no email lookup).
        resp = api.post(
            "/api/auth/login",
            json={"username": "email-user@pharmacy.local", "password": "EmailUser-Pw1!"},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "invalid_credentials"
        # The username field works.
        resp = api.post("/api/auth/login", json={"username": "email-user", "password": "EmailUser-Pw1!"})
        assert resp.status_code == 200


class _NonTtyStdin:
    def isatty(self):
        return False

    def read(self, *args, **kwargs):
        return ""

    def readline(self, *args, **kwargs):
        return ""


def clean_user(username: str) -> "User":
    from app.database import SessionLocal
    from app.models.users import User as UserModel

    db = SessionLocal()
    try:
        return db.execute(select(UserModel).where(UserModel.username == username)).scalars().one()
    finally:
        db.close()
