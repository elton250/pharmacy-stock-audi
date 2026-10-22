"""Cancelled and archived counting sessions: visibility, stock impact and audit trail."""
import uuid
from decimal import Decimal

from tests.conftest import FIXTURE_MEDICINE, create_user, login


def _bootstrap(api):
    api.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "adminpass123", "full_name": "Admin"},
    )
    create_user(api, "pharma1", "pharmapass123", ["pharmacist"])
    create_user(api, "counter1", "counterpass123", ["stock_counter"])
    med1 = api.post("/api/medicines", json=FIXTURE_MEDICINE).json()
    med2 = api.post(
        "/api/medicines",
        json={
            "name": "Ibuprofen 400mg",
            "base_unit": "tablet",
            "barcodes": [{"barcode": "8909876543210", "is_primary": True}],
            "packages": [{"name": "Blister", "units_per_parent": 10, "sort_order": 1}],
        },
    ).json()
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    return med1, med2


def _count(api, session_id, qty=100):
    resp = api.post(
        f"/api/counting/sessions/{session_id}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": qty}]},
    )
    assert resp.status_code == 201, resp.text


def test_cancelled_session_does_not_contribute_to_physical_stock(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"], 100)
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    # No completed count exists -> nothing to report as physical stock
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    report = api.get("/api/reports/variance", params={"medicine_id": med1["id"]}).json()
    row = report["items"][0]
    assert row["physical_stock"] is None
    assert row["classification"] == "UNCOUNTED"


def test_completed_session_contributes_to_physical_stock(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"], 100)
    api.post(f"/api/counting/sessions/{session['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/review", json={"decision": "APPROVED"})

    report = api.get("/api/reports/variance", params={"medicine_id": med1["id"]}).json()
    row = report["items"][0]
    assert Decimal(row["physical_stock"]) == Decimal("100")


def test_recount_supersedes_the_previous_count(api):
    """A later completed count replaces the earlier one instead of adding to it."""
    med1, _ = _bootstrap(api)
    first = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, first["id"], 100)
    api.post(f"/api/counting/sessions/{first['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{first['id']}/review", json={"decision": "APPROVED"})

    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    second = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, second["id"], 80)
    api.post(f"/api/counting/sessions/{second['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{second['id']}/review", json={"decision": "APPROVED"})

    report = api.get("/api/reports/variance", params={"medicine_id": med1["id"]}).json()
    row = report["items"][0]
    assert Decimal(row["physical_stock"]) == Decimal("80")


def test_cancelled_session_is_hidden_from_normal_list_and_archived(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    # Cancelled sessions are visible in the list (they are part of the record) …
    listed = api.get("/api/counting/sessions", params={"status": "CANCELLED"}).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == session["id"]

    # … but a manager can archive them to remove them from the normal view.
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/archive", json={"reason": "Accidental duplicate session"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["archived_at"] is not None
    assert body["archived_by_username"] == "pharma1"
    assert body["archive_reason"] == "Accidental duplicate session"

    listed = api.get("/api/counting/sessions").json()
    assert session["id"] not in [s["id"] for s in listed["items"]]

    # The record is preserved and can be inspected with include_archived
    listed = api.get("/api/counting/sessions", params={"include_archived": True}).json()
    assert session["id"] in [s["id"] for s in listed["items"]]


def test_archive_requires_permission(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    # stock_counter has count:perform but not count:manage
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/archive", json={"reason": "not allowed"}
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_include_archived_requires_permission(api):
    med1, _ = _bootstrap(api)
    resp = api.get("/api/counting/sessions", params={"include_archived": True})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_completed_session_cannot_be_archived(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"])
    api.post(f"/api/counting/sessions/{session['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/review", json={"decision": "APPROVED"})

    resp = api.post(
        f"/api/counting/sessions/{session['id']}/archive", json={"reason": "should be rejected"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "completed_session"


def test_active_session_cannot_be_archived(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/archive", json={"reason": "should be rejected"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_state"


def test_unarchive_restores_the_session(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/archive", json={"reason": "duplicate"})

    resp = api.post(f"/api/counting/sessions/{session['id']}/unarchive")
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived_at"] is None

    listed = api.get("/api/counting/sessions").json()
    assert session["id"] in [s["id"] for s in listed["items"]]


def test_archived_cancelled_session_is_preserved_and_excluded_from_stock(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"], 100)
    api.post(f"/api/counting/sessions/{session['id']}/cancel")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/archive", json={"reason": "duplicate"})

    # The session and its events are still stored …
    detail = api.get(f"/api/counting/sessions/{session['id']}").json()
    assert detail["status"] == "CANCELLED"
    assert len(detail["events"]) == 1
    assert Decimal(detail["total_counted_base_units"]) == Decimal("100")

    # … but it contributes nothing to physical stock
    report = api.get("/api/reports/variance", params={"medicine_id": med1["id"]}).json()
    assert report["items"][0]["physical_stock"] is None


def test_archive_is_written_to_the_audit_log(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/archive", json={"reason": "duplicate"})

    # auditlog:view is administrator-only
    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    logs = api.get("/api/audit-logs", params={"action": "counting.session_archive"}).json()
    assert logs["total"] >= 1
    entry = logs["items"][0]
    assert entry["entity_type"] == "counting_session"
    assert str(entry["entity_id"]) == str(session["id"])
    assert entry["actor_username"] == "pharma1"
