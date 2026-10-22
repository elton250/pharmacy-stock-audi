"""Audit-log generation for key activities."""
from tests.conftest import FIXTURE_MEDICINE, create_user, login


def _setup(api):
    api.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "adminpass123", "full_name": "Admin"},
    )


def test_login_and_setup_logged(admin_api):
    logs = admin_api.get("/api/audit-logs").json()
    actions = {r["action"] for r in logs["items"]}
    assert "user.setup_admin" in actions


def test_failed_login_logged(api):
    _setup(api)
    api.post("/api/auth/logout")
    api.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    api.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    logs = api.get("/api/audit-logs", params={"action": "auth.login_failed"}).json()
    assert logs["total"] >= 1


def test_medicine_and_barcode_events_logged(admin_api):
    admin_api.post("/api/medicines", json=FIXTURE_MEDICINE)
    logs = admin_api.get("/api/audit-logs").json()
    actions = {r["action"] for r in logs["items"]}
    assert "medicine.create" in actions
    entity_rows = admin_api.get(
        "/api/audit-logs", params={"entity_type": "medicine", "entity_id": "1"}
    ).json()
    assert entity_rows["total"] >= 1


def test_inventory_and_counting_events_logged(admin_api):
    from tests.conftest import create_user, login
    import uuid

    med = admin_api.post(
        "/api/medicines", json=FIXTURE_MEDICINE | {"opening_stock": 100}
    ).json()
    admin_api.post(
        "/api/inventory/transactions",
        json={"medicine_id": med["id"], "transaction_type": "RECEIVED", "quantity": 20},
    )
    create_user(admin_api, "counter1", "counterpass123", ["stock_counter"])
    create_user(admin_api, "pharma1", "pharmapass123", ["pharmacist"])
    admin_api.post("/api/auth/logout")
    login(admin_api, "counter1", "counterpass123")
    session = admin_api.post("/api/counting/sessions", json={"medicine_id": med["id"]}).json()
    admin_api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 100}]},
    )
    admin_api.post(f"/api/counting/sessions/{session['id']}/submit")
    admin_api.post("/api/auth/logout")
    login(admin_api, "pharma1", "pharmapass123")
    admin_api.post(f"/api/counting/sessions/{session['id']}/review", json={"decision": "APPROVED"})

    # Audit-log viewing requires auditlog:view (administrator)
    admin_api.post("/api/auth/logout")
    login(admin_api, "admin", "adminpass123")
    logs = admin_api.get("/api/audit-logs", params={"page_size": 200}).json()
    actions = {r["action"] for r in logs["items"]}
    assert "inventory.transaction" in actions
    assert "counting.session_create" in actions
    assert "counting.count_event" in actions
    assert "counting.submit" in actions
    assert "counting.review" in actions


def test_user_management_logged(admin_api):
    create_user(admin_api, "someone", "somepass1234", ["stock_counter"])
    users = admin_api.get("/api/users", params={"search": "someone"}).json()
    uid = users["items"][0]["id"]
    admin_api.patch(f"/api/users/{uid}", json={"is_active": False})
    logs = admin_api.get("/api/audit-logs", params={"entity_type": "user", "entity_id": str(uid)}).json()
    actions = {r["action"] for r in logs["items"]}
    assert "user.create" in actions
    assert "user.update" in actions


def test_audit_logs_never_contain_passwords(admin_api):
    create_user(admin_api, "secretuser", "supersecretpass9", ["stock_counter"])
    logs = admin_api.get("/api/audit-logs", params={"page_size": 200}).json()
    import json as _json

    blob = _json.dumps(logs["items"]).lower()
    assert "supersecretpass9" not in blob
    assert "password" not in blob.replace("password_hash", "")
