"""Authentication: setup, login, logout, sessions, rate limiting, password hashing."""
from app.security import hash_password, verify_password


def test_password_hashing_roundtrip():
    h = hash_password("s3cure-Passw0rd!")
    assert h != "s3cure-Passw0rd!"
    assert verify_password(h, "s3cure-Passw0rd!")
    assert not verify_password(h, "wrong-password")


def test_initial_setup_creates_admin(api):
    resp = api.get("/api/auth/setup-available")
    assert resp.json() == {"available": True}

    resp = api.post(
        "/api/auth/setup",
        json={"username": "rootadmin", "password": "strongpass1", "full_name": "Root"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["username"] == "rootadmin"
    assert any(r["name"] == "administrator" for r in body["user"]["roles"])

    # Session cookie issued on setup
    resp = api.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "rootadmin"


def test_setup_disabled_after_first_admin(admin_api):
    resp = admin_api.post(
        "/api/auth/setup",
        json={"username": "second", "password": "anotherpass1", "full_name": "Second"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "setup_disabled"


def test_setup_available_false_after_setup(admin_api):
    resp = admin_api.get("/api/auth/setup-available")
    assert resp.json() == {"available": False}


def test_login_success_and_failure(api):
    api.post("/api/auth/setup", json={"username": "admin", "password": "adminpass123", "full_name": "A"})
    api.post("/api/auth/logout")

    resp = api.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "admin"

    api.post("/api/auth/logout")
    resp = api.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"

    resp = api.post("/api/auth/login", json={"username": "ghost", "password": "whatever123"})
    assert resp.status_code == 401  # same message: no user enumeration


def test_logout_revokes_session(admin_api):
    admin_api.post("/api/auth/logout")
    resp = admin_api.get("/api/auth/me")
    assert resp.status_code == 401


def test_unauthenticated_access_rejected(api):
    resp = api.get("/api/medicines")
    assert resp.status_code == 401


def test_csrf_required_for_mutations(api):
    api.post("/api/auth/setup", json={"username": "admin", "password": "adminpass123", "full_name": "A"})
    # Mutating call without CSRF header must fail
    resp = api.client.post("/api/medicines", json={"name": "No CSRF"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "csrf_failed"


def test_login_rate_limiting(api):
    api.post("/api/auth/setup", json={"username": "admin", "password": "adminpass123", "full_name": "A"})
    api.post("/api/auth/logout")
    for _ in range(5):
        resp = api.post("/api/auth/login", json={"username": "admin", "password": "bad-password"})
        assert resp.status_code == 401
    resp = api.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert resp.status_code == 429
    assert resp.json()["code"] == "rate_limited"


def test_inactive_user_cannot_login(admin_api):
    from tests.conftest import create_user, login

    create_user(admin_api, "tempuser", "temppass1234", ["stock_counter"])
    admin_api.post("/api/auth/logout")
    login(admin_api, "tempuser", "temppass1234")
    admin_api.post("/api/auth/logout")

    login(admin_api, "admin", "adminpass123")
    users = admin_api.get("/api/users", params={"search": "tempuser"}).json()
    user_id = users["items"][0]["id"]
    resp = admin_api.patch(f"/api/users/{user_id}", json={"is_active": False})
    assert resp.status_code == 200
    admin_api.post("/api/auth/logout")

    resp = admin_api.post("/api/auth/login", json={"username": "tempuser", "password": "temppass1234"})
    assert resp.status_code == 401
