"""End-to-end physical stock audit: expected vs physical, shortage/surplus/match."""
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

    def med(name: str, opening: int):
        payload = dict(FIXTURE_MEDICINE)
        payload["name"] = name
        payload["opening_stock"] = opening
        payload["barcodes"] = []  # avoid duplicate barcode across the three medicines
        resp = api.post("/api/medicines", json=payload)
        assert resp.status_code == 201, resp.text
        return resp.json()

    shortage = med("ShortMed", 100)
    surplus = med("SurplusMed", 50)
    match = med("MatchMed", 80)
    # Movement: shortage med expected 100+50-30 = 120
    api.post(
        "/api/inventory/transactions",
        json={"medicine_id": shortage["id"], "transaction_type": "RECEIVED", "quantity": 50},
    )
    api.post(
        "/api/inventory/transactions",
        json={"medicine_id": shortage["id"], "transaction_type": "ISSUED", "quantity": 30},
    )
    return shortage, surplus, match


def _count_and_complete(api, medicine, qty, audit_id=None):
    session = api.post(
        "/api/counting/sessions", json={"medicine_id": medicine["id"], "stock_audit_id": audit_id}
    ).json()
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": qty}]},
    )
    assert resp.status_code == 201, resp.text
    api.post(f"/api/counting/sessions/{session['id']}/submit")
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/review", json={"decision": "APPROVED"}
    )
    assert resp.status_code == 200, resp.text
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    return session


def test_shortage_surplus_match_workflow(api):
    shortage, surplus, match = _bootstrap(api)

    # Pharmacist initiates an audit covering all three medicines
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(
        "/api/audits",
        json={
            "title": "October stock audit",
            "medicine_ids": [shortage["id"], surplus["id"], match["id"]],
        },
    )
    assert resp.status_code == 201, resp.text
    audit = resp.json()
    assert len(audit["items"]) == 3
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")

    # Physical counts: 115 (expected 120 → shortage), 57 (expected 50 → surplus), 80 (match)
    _count_and_complete(api, shortage, 115, audit_id=audit["id"])
    _count_and_complete(api, surplus, 57, audit_id=audit["id"])
    _count_and_complete(api, match, 80, audit_id=audit["id"])

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass133" if False else "pharmapass123")
    report = api.get("/api/reports/variance", params={"audit_id": audit["id"]}).json()
    by_name = {r["medicine_name"]: r for r in report["items"]}

    s = by_name["ShortMed"]
    assert Decimal(s["expected_stock"]) == Decimal("120")
    assert Decimal(s["physical_stock"]) == Decimal("115")
    assert Decimal(s["variance"]) == Decimal("-5")
    assert s["classification"] == "SHORTAGE"
    assert s["counting_operator"] == "counter1"
    assert s["reviewer"] == "pharma1"

    su = by_name["SurplusMed"]
    assert Decimal(su["variance"]) == Decimal("7")
    assert su["classification"] == "SURPLUS"

    m = by_name["MatchMed"]
    assert Decimal(m["variance"]) == Decimal("0")
    assert m["classification"] == "MATCH"

    # Complete the audit
    resp = api.post(f"/api/audits/{audit['id']}/complete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"

    # Completed audit detail shows persisted variances
    detail = api.get(f"/api/audits/{audit['id']}").json()
    assert detail["status"] == "COMPLETED"
    classes = {i["medicine_name"]: i["classification"] for i in detail["items"]}
    assert classes["ShortMed"] == "SHORTAGE"
    assert classes["SurplusMed"] == "SURPLUS"
    assert classes["MatchMed"] == "MATCH"


def test_variance_not_automatically_applied(api):
    """Completing a count must not silently change inventory."""
    shortage, _, _ = _bootstrap(api)
    _count_and_complete(api, shortage, 100)  # expected 120 → shortage of 20
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    med = api.get(f"/api/medicines/{shortage['id']}").json()
    assert Decimal(med["expected_stock"]) == Decimal("120")  # unchanged
    report = api.get("/api/reports/variance", params={"medicine_id": shortage["id"]}).json()
    row = report["items"][0]
    assert Decimal(row["variance"]) == Decimal("-20")
    assert row["classification"] == "SHORTAGE"
    # Only the two original transactions exist
    txns = api.get("/api/inventory/transactions", params={"medicine_id": shortage["id"]}).json()
    assert txns["total"] == 3  # opening + received + issued


def test_stock_levels_report(admin_api):
    resp = admin_api.post(
        "/api/medicines", json=FIXTURE_MEDICINE | {"name": "LowMed", "opening_stock": 10, "reorder_level": 50}
    )
    mid = resp.json()["id"]
    report = admin_api.get("/api/reports/stock-levels").json()
    row = next(r for r in report["items"] if r["medicine_id"] == mid)
    assert row["below_reorder"] is True
    assert Decimal(row["expected_stock"]) == Decimal("10")


def test_cannot_complete_audit_with_open_sessions(api):
    shortage, surplus, match = _bootstrap(api)
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    audit = api.post(
        "/api/audits", json={"title": "Open sessions audit", "medicine_ids": [shortage["id"]]}
    ).json()
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    session = api.post(
        "/api/counting/sessions", json={"medicine_id": shortage["id"], "stock_audit_id": audit["id"]}
    ).json()
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(f"/api/audits/{audit['id']}/complete")
    assert resp.status_code == 409
    assert resp.json()["code"] == "sessions_open"


def test_audit_item_without_a_completed_count_stays_uncounted(api):
    """A medicine that was never counted (or only cancelled) must not look like a shortage."""
    shortage, _, _ = _bootstrap(api)
    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    audit = api.post(
        "/api/audits", json={"title": "Uncounted audit", "medicine_ids": [shortage["id"]]}
    ).json()

    # A cancelled session linked to the audit contributes nothing
    api.post("/api/auth/logout")
    login(api, "counter1", "counterpass123")
    session = api.post(
        "/api/counting/sessions", json={"medicine_id": shortage["id"], "stock_audit_id": audit["id"]}
    ).json()
    resp = api.post(
        f"/api/counting/sessions/{session['id']}/counts",
        json={"idempotency_key": str(uuid.uuid4()), "lines": [{"quantity": 115}]},
    )
    assert resp.status_code == 201, resp.text
    api.post(f"/api/counting/sessions/{session['id']}/cancel")

    api.post("/api/auth/logout")
    login(api, "pharma1", "pharmapass123")
    resp = api.post(f"/api/audits/{audit['id']}/complete")
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["physical_stock"] is None
    assert item["variance"] is None
    assert item["classification"] == "UNCOUNTED"

    report = api.get("/api/reports/variance", params={"audit_id": audit["id"]}).json()
    row = report["items"][0]
    assert row["physical_stock"] is None
    assert row["classification"] == "UNCOUNTED"
