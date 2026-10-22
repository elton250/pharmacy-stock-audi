"""Only ONE active counting session may exist at a time (API + database level)."""
import threading
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models.counting import CountingSession, SessionStatus
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


def _count(api, session_id, qty=5):
    resp = api.post(
        f"/api/counting/sessions/{session_id}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": qty}]},
    )
    assert resp.status_code == 201, resp.text


def test_second_session_rejected_while_one_is_active(api):
    med1, med2 = _bootstrap(api)
    first = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]})
    assert first.status_code == 201

    second = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert second.status_code == 409
    assert second.json()["code"] == "active_session_exists"
    assert first.json()["code"] in second.json()["detail"]


def test_draft_session_also_blocks_a_new_session(api):
    med1, med2 = _bootstrap(api)
    draft = api.post("/api/counting/sessions", json={})
    assert draft.status_code == 201
    assert draft.json()["status"] == "DRAFT"

    second = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert second.status_code == 409
    assert second.json()["code"] == "active_session_exists"


def test_new_session_allowed_after_cancelling(api):
    med1, med2 = _bootstrap(api)
    first = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    assert api.post(f"/api/counting/sessions/{first['id']}/cancel").status_code == 200

    second = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert second.status_code == 201
    assert second.json()["active_medicine_id"] == med2["id"]


def test_new_session_allowed_after_completing(api):
    med1, med2 = _bootstrap(api)
    first = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, first["id"])
    api.post(f"/api/counting/sessions/{first['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    approved = api.post(
        f"/api/counting/sessions/{first['id']}/review", json={"decision": "APPROVED"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "COMPLETED"

    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    second = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert second.status_code == 201


def test_active_session_endpoint_reports_the_open_session(api):
    med1, _ = _bootstrap(api)
    assert api.get("/api/counting/sessions/active").json() is None

    created = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    active = api.get("/api/counting/sessions/active").json()
    assert active["id"] == created["id"]
    assert active["status"] == "IN_PROGRESS"

    api.post(f"/api/counting/sessions/{created['id']}/cancel")
    assert api.get("/api/counting/sessions/active").json() is None


def test_database_index_blocks_two_active_sessions():
    """Bypassing the API entirely, the database still refuses a second active row."""
    results: list[str] = []
    barrier = threading.Barrier(2)

    def _insert(name: str) -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            db.add(
                CountingSession(
                    code=f"CS-TEST-{name}",
                    status=SessionStatus.IN_PROGRESS.value,
                    active_medicine_id=None,
                    created_by=1,
                )
            )
            db.commit()
            results.append("ok")
        except IntegrityError:
            db.rollback()
            results.append("integrity_error")
        except Exception as exc:  # pragma: no cover - defensive
            db.rollback()
            results.append(f"error:{exc.__class__.__name__}")
        finally:
            db.close()

    threads = [threading.Thread(target=_insert, args=(str(i),)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    assert "ok" in results
    assert "integrity_error" in results


def test_only_one_active_session_row_in_database(api):
    med1, med2 = _bootstrap(api)
    api.post("/api/counting/sessions", json={"medicine_id": med1["id"]})
    api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})

    db = SessionLocal()
    try:
        active_rows = (
            db.query(CountingSession)
            .filter(CountingSession.status.in_(["DRAFT", "IN_PROGRESS", "PENDING_REVIEW"]))
            .all()
        )
        assert len(active_rows) == 1
    finally:
        db.close()


def test_medicine_of_active_session_is_immutable(api):
    med1, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    resp = api.post(f"/api/counting/sessions/{session['id']}/activate", json={"medicine_id": med2["id"]})
    assert resp.status_code == 409
    assert resp.json()["code"] in ("invalid_state", "medicine_mismatch")

    current = api.get(f"/api/counting/sessions/{session['id']}").json()
    assert current["active_medicine_id"] == med1["id"]


def test_creating_a_session_for_a_second_user_is_also_blocked(api):
    med1, med2 = _bootstrap(api)
    api.post("/api/counting/sessions", json={"medicine_id": med1["id"]})

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert resp.status_code == 409
    assert resp.json()["code"] == "active_session_exists"


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
def test_terminal_sessions_do_not_block_new_sessions(api, status):
    med1, med2 = _bootstrap(api)
    first = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, first["id"])
    if status == "COMPLETED":
        api.post(f"/api/counting/sessions/{first['id']}/submit")
        api.post("/api/auth/logout")
        login(api, "pharma1", "pharmapass123")
        api.post(f"/api/counting/sessions/{first['id']}/review", json={"decision": "APPROVED"})
        api.post("/api/auth/logout")
        login(api, "counter1", "counterpass123")
    else:
        api.post(f"/api/counting/sessions/{first['id']}/cancel")

    resp = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert resp.status_code == 201
