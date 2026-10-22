"""Static Counting session recovery: resume, cancel and stuck-session release.

Covers the recovery workflow end to end:

* the active session is visible with its details (id, code, medicine, barcode,
  start time, status);
* Resume reuses the SAME session: no new session, no duplicated count events or
  inventory transactions, no reset quantities, no lost progress;
* Cancel is authorised for the creator and for holders of ``count:manage``;
* the cancellation is recorded in the audit trail with the acting user;
* cancelled sessions stay excluded from physical stock / variance / audit sums;
* the single-active-session rule survives concurrent requests.
"""
import threading
import time
import uuid
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models.counting import CountingSession, SessionStatus
from app.services.counting import lock_active_session_slot
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
    return resp.json()


# --------------------------------------------------------------------------- #
# The active session is visible with all the details an operator needs
# --------------------------------------------------------------------------- #


def test_active_session_endpoint_exposes_the_details_needed_for_recovery(api):
    med1, _ = _bootstrap(api)
    created = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    active = api.get("/api/counting/sessions/active").json()
    assert active is not None
    assert active["id"] == created["id"]
    assert active["code"] == created["code"]
    assert active["status"] == "IN_PROGRESS"
    assert active["active_medicine_name"] == FIXTURE_MEDICINE["name"]
    assert active["active_medicine_barcode"] == FIXTURE_MEDICINE["barcodes"][0]["barcode"]
    assert active["created_at"]
    assert active["creator_username"] == "counter1"
    assert active["total_counted_base_units"] == "0"


def test_active_session_is_null_when_nothing_is_open(api):
    _bootstrap(api)
    assert api.get("/api/counting/sessions/active").json() is None


# --------------------------------------------------------------------------- #
# Resume reuses the existing session
# --------------------------------------------------------------------------- #


def test_resume_returns_the_same_session_with_its_progress(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"], 100)
    _count(api, session["id"], 20)

    resp = api.post(f"/api/counting/sessions/{session['id']}/resume")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Same session, not a new one.
    assert body["id"] == session["id"]
    assert body["code"] == session["code"]
    assert body["status"] == "IN_PROGRESS"
    # Progress kept: both count events and their total are still there.
    assert len(body["events"]) == 2
    assert Decimal(body["total_counted_base_units"]) == Decimal("120")

    # …and no additional session was created.
    listed = api.get("/api/counting/sessions").json()
    assert listed["total"] == 1


def test_resume_does_not_duplicate_count_events_or_inventory(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"], 100)

    for _ in range(3):
        api.post(f"/api/counting/sessions/{session['id']}/resume")

    detail = api.get(f"/api/counting/sessions/{session['id']}").json()
    assert len(detail["events"]) == 1
    assert Decimal(detail["total_counted_base_units"]) == Decimal("100")

    # No inventory transaction was produced by resuming.
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    txns = api.get("/api/inventory/transactions", params={"medicine_id": med1["id"]}).json()
    assert txns["total"] == 0


def test_resume_of_a_draft_session_is_allowed(api):
    med1, _ = _bootstrap(api)
    draft = api.post("/api/counting/sessions", json={}).json()
    assert draft["status"] == "DRAFT"

    resp = api.post(f"/api/counting/sessions/{draft['id']}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DRAFT"
    assert resp.json()["id"] == draft["id"]


def test_resume_of_a_pending_review_session_is_allowed(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"])
    api.post(f"/api/counting/sessions/{session['id']}/submit")

    resp = api.post(f"/api/counting/sessions/{session['id']}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "PENDING_REVIEW"
    assert len(resp.json()["events"]) == 1


def test_resume_of_a_completed_session_is_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"])
    api.post(f"/api/counting/sessions/{session['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/review", json={"decision": "APPROVED"})

    resp = api.post(f"/api/counting/sessions/{session['id']}/resume")
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_completed"


def test_resume_of_a_cancelled_session_is_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    resp = api.post(f"/api/counting/sessions/{session['id']}/resume")
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_cancelled"


def test_resume_of_a_missing_session_returns_404(api):
    _bootstrap(api)
    resp = api.post("/api/counting/sessions/999999/resume")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_resume_of_an_archived_session_is_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/archive", json={"reason": "duplicate"})

    resp = api.post(f"/api/counting/sessions/{session['id']}/resume")
    assert resp.status_code == 409
    assert resp.json()["code"] == "archived"


def test_resume_by_an_unrelated_operator_is_forbidden(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    # A second stock_counter is neither the creator nor a count:manage holder.
    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    create_user(api, "counter2", "counterpass123", ["stock_counter"])
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")

    resp = api.post(f"/api/counting/sessions/{session['id']}/resume")
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_resume_is_written_to_the_audit_log(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/resume")

    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    logs = api.get("/api/audit-logs", params={"action": "counting.session_resume"}).json()
    assert logs["total"] >= 1
    entry = logs["items"][0]
    assert entry["entity_type"] == "counting_session"
    assert str(entry["entity_id"]) == str(session["id"])
    assert entry["actor_username"] == "counter1"


# --------------------------------------------------------------------------- #
# Cancel authorisation
# --------------------------------------------------------------------------- #


def test_creator_can_cancel_their_own_session(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "CANCELLED"
    assert body["cancelled_at"] is not None
    assert body["cancelled_by_username"] == "counter1"


def test_manager_can_cancel_a_session_created_by_someone_else(api):
    """A stuck session must be releasable by a supervisor — this was the blocker."""
    med1, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")  # pharmacist holds count:manage
    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "CANCELLED"
    assert body["cancelled_by_username"] == "pharma1"

    # …and the system is unblocked again.
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    new_session = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert new_session.status_code == 201, new_session.text


def test_unrelated_operator_cannot_cancel_someone_elses_session(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    create_user(api, "counter2", "counterpass123", ["stock_counter"])
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")

    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"

    # The session is untouched and still blocks new sessions.
    detail = api.get(f"/api/counting/sessions/{session['id']}").json()
    assert detail["status"] == "IN_PROGRESS"
    assert detail["cancelled_at"] is None


def test_cancelling_a_completed_session_is_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"])
    api.post(f"/api/counting/sessions/{session['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/review", json={"decision": "APPROVED"})

    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_completed"


def test_cancelling_an_already_cancelled_session_is_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    assert api.post(f"/api/counting/sessions/{session['id']}/cancel").status_code == 200

    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_cancelled"


def test_cancelling_a_missing_session_returns_404(api):
    _bootstrap(api)
    resp = api.post("/api/counting/sessions/999999/cancel")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_cancelling_an_archived_session_is_rejected(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(f"/api/counting/sessions/{session['id']}/archive", json={"reason": "duplicate"})

    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.status_code == 409
    assert resp.json()["code"] == "archived"


def test_cancel_releases_the_single_active_session_rule(api):
    med1, med2 = _bootstrap(api)
    first = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    blocked = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "active_session_exists"

    assert api.post(f"/api/counting/sessions/{first['id']}/cancel").status_code == 200
    assert api.get("/api/counting/sessions/active").json() is None

    second = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert second.status_code == 201, second.text


def test_cancel_is_written_to_the_audit_log_with_the_acting_user(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    logs = api.get("/api/audit-logs", params={"action": "counting.cancel"}).json()
    assert logs["total"] >= 1
    entry = logs["items"][0]
    assert entry["entity_type"] == "counting_session"
    assert str(entry["entity_id"]) == str(session["id"])
    assert entry["actor_username"] == "counter1"
    assert entry["created_at"]


def test_cancel_stores_the_optional_reason_in_the_audit_log(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    resp = api.post(
        f"/api/counting/sessions/{session['id']}/cancel",
        json={"reason": "Operator was called away"},
    )
    assert resp.status_code == 200, resp.text

    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    logs = api.get("/api/audit-logs", params={"action": "counting.cancel"}).json()
    entry = logs["items"][0]
    assert entry["after_data"]["reason"] == "Operator was called away"
    assert entry["after_data"]["status"] == "CANCELLED"
    assert entry["after_data"]["cancelled_by"] is not None


def test_cancel_reason_is_optional(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    # No body at all.
    resp = api.post(f"/api/counting/sessions/{session['id']}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CANCELLED"


def test_cancel_rejects_an_overlong_reason(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    resp = api.post(
        f"/api/counting/sessions/{session['id']}/cancel", json={"reason": "x" * 501}
    )
    assert resp.status_code == 422


def test_cancel_records_the_actor_on_the_session_row(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    db = SessionLocal()
    try:
        row = db.get(CountingSession, session["id"])
        assert row is not None
        assert row.cancelled_by is not None
        assert row.cancelled_at is not None
        assert row.status == SessionStatus.CANCELLED.value
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Cancelled sessions never influence stock figures
# --------------------------------------------------------------------------- #


def test_cancelled_session_is_excluded_from_variance_and_physical_stock(api):
    med1, _ = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()
    _count(api, session["id"], 100)
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    report = api.get("/api/reports/variance", params={"medicine_id": med1["id"]}).json()
    row = report["items"][0]
    assert row["physical_stock"] is None
    assert row["variance"] is None
    assert row["classification"] == "UNCOUNTED"


def test_cancelled_session_does_not_block_the_audit_summary(api):
    med1, _ = _bootstrap(api)
    # Creating an audit needs audit:initiate (pharmacist), counting needs count:perform.
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    audit = api.post(
        "/api/audits", json={"title": "Weekly count", "audit_date": "2026-10-21", "medicine_ids": [med1["id"]]}
    ).json()
    assert "id" in audit, audit

    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    session = api.post(
        "/api/counting/sessions", json={"medicine_id": med1["id"], "stock_audit_id": audit["id"]}
    ).json()
    _count(api, session["id"], 100)
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    detail = api.get(f"/api/audits/{audit['id']}").json()
    item = detail["items"][0]
    assert item["physical_stock"] is None
    assert item["variance"] is None
    assert item["classification"] == "UNCOUNTED"


# --------------------------------------------------------------------------- #
# The single-active-session rule under concurrency
# --------------------------------------------------------------------------- #


def test_concurrent_session_creation_still_yields_only_one_active_row(api):
    med1, med2 = _bootstrap(api)
    results: list[str] = []
    barrier = threading.Barrier(2)

    def _create(medicine_id: int, name: str) -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            db.add(
                CountingSession(
                    code=f"CS-RACE-{name}",
                    status=SessionStatus.IN_PROGRESS.value,
                    active_medicine_id=medicine_id,
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

    threads = [threading.Thread(target=_create, args=(med1["id"], str(i))) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    assert "ok" in results
    assert "integrity_error" in results

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


def test_only_one_active_session_exists_after_resume(api):
    med1, med2 = _bootstrap(api)
    session = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    api.post(f"/api/counting/sessions/{session['id']}/resume")

    db = SessionLocal()
    try:
        active_rows = (
            db.query(CountingSession)
            .filter(CountingSession.status.in_(["DRAFT", "IN_PROGRESS", "PENDING_REVIEW"]))
            .all()
        )
        assert len(active_rows) == 1
        assert active_rows[0].id == session["id"]
    finally:
        db.close()


def test_advisory_lock_serialises_the_active_session_slot():
    """Two transactions cannot hold the "active session slot" at the same time."""
    order: list[str] = []
    barrier = threading.Barrier(2)

    def _hold(name: str) -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            lock_active_session_slot(db)
            order.append(f"{name}-in")
            time.sleep(0.4)
            order.append(f"{name}-out")
            db.commit()
        finally:
            db.close()

    threads = [threading.Thread(target=_hold, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # Mutual exclusion: the two critical sections never interleave.
    assert len(order) == 4
    assert order[1].split("-")[0] == order[0].split("-")[0]


def test_create_session_reports_the_existing_active_session(api):
    med1, med2 = _bootstrap(api)
    first = api.post("/api/counting/sessions", json={"medicine_id": med1["id"]}).json()

    blocked = api.post("/api/counting/sessions", json={"medicine_id": med2["id"]})
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["code"] == "active_session_exists"
    # The message tells the operator which session is in the way.
    assert first["code"] in body["detail"]
