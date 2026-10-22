"""Review authorisation policy and counter/reviewer identity.

The policy under test:

* only a holder of ``count:review`` may review a counting session;
* the creator of a session may not review it;
* a user who recorded a count event on a session may not review it;
* any other authorised user may review it.

Identity under test: the audit item must show the operator who counted as the
counter and the approver as the reviewer - never the other way round.
"""
import uuid

from app.models.counting import CountEvent
from tests.conftest import create_user, login

OPENING_STOCK = 300


def _setup(api) -> dict:
    """Admin + a counter that may also review + a medicine + a stock audit."""
    resp = api.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "adminpass123", "full_name": "Admin"},
    )
    assert resp.status_code == 201, resp.text
    # "counter1" has both roles: count:perform to count and count:review to
    # review. Only the self-review rule can stop this user from reviewing.
    create_user(api, "counter1", "counterpass123", ["stock_counter", "pharmacist"])
    create_user(api, "counter2", "counterpass123", ["stock_counter"])
    create_user(api, "pharma1", "pharmapass123", ["pharmacist"])

    med = api.post(
        "/api/medicines",
        json={
            "name": "Audit Test Medicine",
            "base_unit": "tablet",
            "opening_stock": OPENING_STOCK,
        },
    ).json()
    audit = api.post(
        "/api/audits",
        json={"title": "Audit Test Audit", "medicine_ids": [med["id"]]},
    ).json()
    users = api.get("/api/users", params={"page_size": 50}).json()["items"]
    ids = {u["username"]: u["id"] for u in users}
    return {"medicine": med, "audit": audit, "ids": ids}


def _count_and_submit(api, medicine_id: int, audit_id: int, quantity: int) -> dict:
    """Create a session, record a count and submit it for review."""
    session = api.post(
        "/api/counting/sessions",
        json={"medicine_id": medicine_id, "stock_audit_id": audit_id},
    ).json()
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={
            "medicine_id": medicine_id,
            "idempotency_key": str(uuid.uuid4()),
            "lines": [{"quantity": quantity}],
        },
    )
    assert resp.status_code == 201, resp.text
    resp = api.post(f"/api/counting/sessions/{session['id']}/submit")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "PENDING_REVIEW"
    return resp.json()


def test_creator_cannot_review_own_count(api):
    """The creator may hold count:review but must not review their own count."""
    setup = _setup(api)
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")

    session = _count_and_submit(api, setup["medicine"]["id"], setup["audit"]["id"], 275)
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "self_review"
    assert "creator" in body["detail"]


def test_operator_cannot_review_own_count(api, db):
    """A user who recorded the count may not review it, even if not the creator.

    The API only lets the session creator record counts, so the operator branch
    of the policy is exercised by writing the count event directly: this keeps
    the rule covered even if the recording rule is ever widened.
    """
    setup = _setup(api)
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")

    session = api.post(
        "/api/counting/sessions",
        json={"medicine_id": setup["medicine"]["id"], "stock_audit_id": setup["audit"]["id"]},
    ).json()
    assert session["status"] == "IN_PROGRESS"

    pharma = setup["ids"]
    pharma1_id = pharma["pharma1"]

    db.add(
        CountEvent(
            session_id=session["id"],
            medicine_id=setup["medicine"]["id"],
            operator_id=pharma1_id,
            total_base_units=275,
            idempotency_key=str(uuid.uuid4()),
            notes="operator branch fixture",
        )
    )
    db.commit()

    resp = api.post(f"/api/counting/sessions/{session['id']}/submit")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "PENDING_REVIEW"

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "self_review"
    assert "operator" in body["detail"]


def test_distinct_authorized_reviewer_can_review(api):
    setup = _setup(api)
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")
    session = _count_and_submit(api, setup["medicine"]["id"], setup["audit"]["id"], 275)

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED", "notes": "looks good"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["review_decision"] == "APPROVED"
    assert body["reviewed_by"] is not None


def test_role_without_count_review_permission_is_rejected(api):
    """The seeded stock_counter role deliberately has no count:review."""
    setup = _setup(api)
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")
    session = _count_and_submit(api, setup["medicine"]["id"], setup["audit"]["id"], 275)

    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "forbidden"
    assert "count:review" in body["detail"]


def test_audit_item_shows_counter_and_reviewer_separately(api):
    """The counter is the operator; the reviewer is the approver."""
    setup = _setup(api)
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")
    session = _count_and_submit(api, setup["medicine"]["id"], setup["audit"]["id"], 275)

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED"},
    )
    assert resp.status_code == 200, resp.text

    audit = api.get(f"/api/audits/{setup['audit']['id']}").json()
    item = audit["items"][0]
    assert float(item["expected_stock"]) == OPENING_STOCK
    assert float(item["physical_stock"]) == 275
    assert float(item["variance"]) == -25
    assert item["classification"] == "SHORTAGE"
    # The counter is the user who recorded the count ...
    assert item["counter_username"] == "counter2"
    # ... and the reviewer is the user who approved it.
    assert item["reviewer_username"] == "pharma1"
    assert item["counter_username"] != item["reviewer_username"]
    assert item["counted_by"] != item["reviewed_by"]


def test_counted_by_is_not_the_reviewer(api):
    """Regression: counted_by used to point at the reviewer, not the operator."""
    setup = _setup(api)
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")
    session = _count_and_submit(api, setup["medicine"]["id"], setup["audit"]["id"], 275)

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED"},
    )

    users = setup["ids"]
    audit = api.get(f"/api/audits/{setup['audit']['id']}").json()
    item = audit["items"][0]
    assert item["counted_by"] == users["counter2"]
    assert item["reviewed_by"] == users["pharma1"]


def test_audit_history_records_the_reviewer(api):
    """The audit trail keeps the reviewer identity of the counting.review entry."""
    setup = _setup(api)
    api.post("/api/auth/logout")
    login(api, "counter2", "counterpass123")
    session = _count_and_submit(api, setup["medicine"]["id"], setup["audit"]["id"], 275)

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    api.post(
        f"/api/counting/sessions/{session['id']}/review",
        json={"decision": "APPROVED", "notes": "verified"},
    )

    # auditlog:view belongs to the administrator role, not to the pharmacist.
    api.post("/api/auth/logout")
    login(api, "admin", "adminpass123")
    logs = api.get("/api/audit-logs", params={"page_size": 100}).json()["items"]
    review_logs = [entry for entry in logs if entry["action"] == "counting.review"]
    assert len(review_logs) == 1
    assert review_logs[0]["actor_username"] == "pharma1"
    count_logs = [entry for entry in logs if entry["action"] == "counting.count_event"]
    assert len(count_logs) == 1
    assert count_logs[0]["actor_username"] == "counter2"
