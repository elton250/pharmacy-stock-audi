"""Strict static counting: lifecycle, restrictions, idempotency, review."""
import uuid
from decimal import Decimal

from tests.conftest import FIXTURE_MEDICINE, create_user, login


def _bootstrap(api):
    """Admin setup + two medicines + pharmacist reviewer + login as counter."""
    resp = api.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "adminpass123", "full_name": "Admin"},
    )
    assert resp.status_code == 201
    create_user(api, "pharma1", "pharmapass123", ["pharmacist"])
    create_user(api, "counter1", "counterpass123", ["stock_counter"])
    med1 = api.post("/api/medicines", json=FIXTURE_MEDICINE).json()
    med2 = api.post(
        "/api/medicines",
        json={
            "name": "Ibuprofen 400mg",
            "base_unit": "tablet",
            "barcodes": [{"barcode": "8909876543210", "is_primary": True}],
            "packages": [
                {"name": "Blister", "units_per_parent": 10, "sort_order": 1},
                {"name": "Box", "parent_name": "Blister", "units_per_parent": 10, "sort_order": 2},
            ],
        },
    ).json()
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    return med1, med2


def test_full_counting_lifecycle(api):
    med1, med2 = _bootstrap(api)

    # Counter creates a session with active medicine → IN_PROGRESS
    resp = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]})
    assert resp.status_code == 201
    session = resp.json()
    assert session["status"] == "IN_PROGRESS"
    assert session["active_medicine_id"] == med1["id"]

    # Count: 2 boxes + 3 blisters + cut blister(4) + 2 loose = 236
    box = next(p for p in med1["packages"] if p["name"] == "Box")
    blister = next(p for p in med1["packages"] if p["name"] == "Blister")
    key = str(uuid.uuid4())
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={
            "medicine_id": med1["id"],
            "idempotency_key": key,
            "lines": [
                {"package_config_id": box["id"], "quantity": 2},
                {"package_config_id": blister["id"], "quantity": 3},
                {"package_config_id": blister["id"], "quantity": 1, "partial_units": 4},
                {"package_config_id": None, "quantity": 2},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    assert Decimal(resp.json()["total_base_units"]) == Decimal("236")

    # Submit for review
    resp = api.post(f"/api/counting/sessions/{session['id']}/submit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING_REVIEW"

    # Counter cannot review own session
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED"},
    )
    assert resp.status_code == 403

    # Login as pharmacist and approve
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED", "notes": "Looks good"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["review_decision"] == "APPROVED"

    # Completed sessions are immutable
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 5}]},
    )
    assert resp.status_code == 409


def test_reject_different_medicine_during_active_count(api):
    med1, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={
            "medicine_id": med2["id"],
            "idempotency_key": str(uuid.uuid4()),
            "lines": [{"quantity": 1}],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "medicine_mismatch"


def test_no_medicine_id_allowed_when_matching_active(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 5}]},
    )
    assert resp.status_code == 201


def test_never_auto_switch_medicine(api):
    med1, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    # Attempt to "activate" a different medicine on an IN_PROGRESS session is rejected
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/activate", json={"medicine_id": med2["id"]}
    )
    assert resp.status_code == 409
    # Session still on medicine 1
    current = api.get(f"/api/counting/sessions/{session['id']}").json()
    assert current["active_medicine_id"] == med1["id"]


def test_must_complete_or_cancel_before_switching(api):
    med1, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 1}]},
    )
    api.post(f"/api/counting/sessions/{session['id']}/submit")
    # Reviewer rejects → back to IN_PROGRESS (still medicine 1)
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review", json={"decision": "REJECTED", "notes": " recount"}
    )
    assert resp.json()["status"] == "IN_PROGRESS"
    assert resp.json()["active_medicine_id"] == med1["id"]
    # Cancel → now a new session can count medicine 2
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.json()["status"] == "CANCELLED"
    resp = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert resp.status_code == 201


def test_idempotent_duplicate_submission(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    key = str(uuid.uuid4())
    payload = {
        "idempotency_key": key,
        "lines": [{"quantity": 7}],
    }
    r1 = api.post(f"/api/counting/sessions/{session['id']}/counts", json=payload)
    assert r1.status_code == 201
    # Retry with the same key: same event returned, no double counting
    r2 = api.post(f"/api/counting/sessions/{session['id']}/counts", json=payload)
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    detail = api.get(f"/api/counting/sessions/{session['id']}").json()
    assert detail["total_counted_base_units"] == "7.000"
    assert len(detail["events"]) == 1


def test_submit_without_counts_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    resp = api.post(f"/api/counting/sessions/{session['id']}/submit")
    assert resp.status_code == 422
    assert resp.json()["code"] == "no_counts"


def test_counts_from_other_operator_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 1}]},
    )
    assert resp.status_code == 403


def test_package_mismatch_rejected(api):
    med1, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    blister2 = next(p for p in med2["packages"] if p["name"] == "Blister")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "lines": [{"package_config_id": blister2["id"], "quantity": 1}],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "package_mismatch"


def test_draft_session_requires_explicit_activation(api):
    _, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={}).json()
    assert session["status"] == "DRAFT"
    assert session["active_medicine_id"] is None

    # Cannot count without an active medicine
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 1}]},
    )
    assert resp.status_code == 409

    # Explicit activation
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/activate", json={"medicine_id": med2["id"]}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "IN_PROGRESS"
    assert resp.json()["active_medicine_id"] == med2["id"]


def test_counts_persist_for_logged_in_session(api):
    """Saved counts survive getting the session again (persistence within session)."""
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 12}]},
    )
    # "Re-fetch" (same as app restart on the same machine with a valid cookie)
    detail = api.get(f"/api/counting/sessions/{session['id']}").json()
    assert detail["total_counted_base_units"] == "12.000"
    assert detail["status"] == "IN_PROGRESS"
