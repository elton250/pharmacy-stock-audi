"""Role-based access control enforced on the backend."""
from tests.conftest import create_user, login


def _setup_admin_and_users(api) -> None:
    resp = api.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "adminpass123", "full_name": "Site Admin"},
    )
    assert resp.status_code == 201
    create_user(api, "pharma1", "pharmapass123", ["pharmacist"])
    create_user(api, "counter1", "counterpass123", ["stock_counter"])


def test_stock_counter_cannot_create_medicine(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    resp = api.post("/api/medicines", json={"name": "Ibuprofen"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_stock_counter_can_view_medicines(api):
    _setup_admin_and_users(api)
    resp = api.post("/api/medicines", json={"name": "Amoxicillin", "base_unit": "capsule"})
    assert resp.status_code == 201
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    resp = api.get("/api/medicines")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_stock_counter_cannot_manage_users(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    assert api.get("/api/users").status_code == 403
    resp = api.post(
        "/api/users", json={"username": "x", "password": "password123", "full_name": "X"}
    )
    assert resp.status_code == 403


def test_stock_counter_cannot_transact_inventory(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    resp = api.post(
        "/api/inventory/transactions",
        json={"medicine_id": 1, "transaction_type": "RECEIVED", "quantity": 5},
    )
    assert resp.status_code == 403


def test_pharmacist_can_transact_but_not_manage_users(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        "/api/inventory/transactions",
        json={"medicine_id": 999999, "transaction_type": "RECEIVED", "quantity": 5},
    )
    assert resp.status_code == 404  # permission OK, medicine missing
    assert api.get("/api/users").status_code == 403


def test_pharmacist_cannot_create_medicines(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    assert api.post("/api/medicines", json={"name": "Something"}).status_code == 403


def test_pharmacist_can_review_counts(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    assert api.get("/api/counting/sessions").status_code == 200


def test_audit_log_viewer_restricted(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    assert api.get("/api/audit-logs").status_code == 403
    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    assert api.get("/api/audit-logs").status_code == 200


def test_admin_can_manage_roles(api):
    _setup_admin_and_users(api)
    roles = api.get("/api/users/roles/all")
    assert roles.status_code == 200
    names = {r["name"] for r in roles.json()}
    assert {"administrator", "pharmacist", "stock_counter"} <= names


def test_count_manage_permission_is_seeded(api):
    _setup_admin_and_users(api)
    meta = api.get("/api/meta").json()
    assert "count:manage" in meta["permissions"]


def test_stock_counter_cannot_archive_sessions(api):
    _setup_admin_and_users(api)
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    resp = api.post(
        "/api/counting/sessions/1/archive", json={"reason": "not allowed for counters"}
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"
