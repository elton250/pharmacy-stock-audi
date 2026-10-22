"""Medicine master data: creation, search, pagination, editing, deactivation."""
from decimal import Decimal

from tests.conftest import FIXTURE_MEDICINE


def test_create_medicine_with_barcodes_and_packages(admin_api):
    resp = admin_api.post("/api/medicines", json=FIXTURE_MEDICINE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Paracetamol 500mg"
    assert len(body["barcodes"]) == 1
    assert body["barcodes"][0]["barcode"] == "8901234567890"
    assert body["barcodes"][0]["is_primary"] is True
    pkg_names = {p["name"]: p for p in body["packages"]}
    assert Decimal(pkg_names["Blister"]["units_in_base"]) == Decimal("10")
    assert Decimal(pkg_names["Box"]["units_in_base"]) == Decimal("100")  # 10 blisters × 10 tablets
    assert Decimal(body["expected_stock"]) == Decimal("0")


def test_opening_stock_creates_transaction(admin_api):
    medicine = FIXTURE_MEDICINE | {"opening_stock": 250}
    resp = admin_api.post("/api/medicines", json=medicine)
    assert resp.status_code == 201
    assert Decimal(resp.json()["expected_stock"]) == Decimal("250")
    txns = admin_api.get("/api/inventory/transactions", params={"medicine_id": resp.json()["id"]}).json()
    assert txns["total"] == 1
    assert txns["items"][0]["transaction_type"] == "OPENING"


def test_search_and_pagination(admin_api):
    for i in range(30):
        resp = admin_api.post("/api/medicines", json={"name": f"Medicine {i:03d}", "base_unit": "unit"})
        assert resp.status_code == 201
    resp = admin_api.get("/api/medicines", params={"page": 1, "page_size": 10})
    body = resp.json()
    assert body["total"] == 30
    assert len(body["items"]) == 10
    resp = admin_api.get("/api/medicines", params={"page": 3, "page_size": 10})
    assert len(resp.json()["items"]) == 10
    resp = admin_api.get("/api/medicines", params={"search": "Medicine 007"})
    assert resp.json()["total"] == 1


def test_update_medicine(admin_api):
    created = admin_api.post("/api/medicines", json=FIXTURE_MEDICINE).json()
    resp = admin_api.patch(
        f"/api/medicines/{created['id']}",
        json={"manufacturer": "NewPharm", "reorder_level": 200},
    )
    assert resp.status_code == 200
    assert resp.json()["manufacturer"] == "NewPharm"
    assert Decimal(resp.json()["reorder_level"]) == Decimal("200")


def test_opening_stock_locked_after_transactions(admin_api):
    created = admin_api.post(
        "/api/medicines", json=FIXTURE_MEDICINE | {"opening_stock": 100}
    ).json()
    admin_api.post(
        "/api/inventory/transactions",
        json={"medicine_id": created["id"], "transaction_type": "RECEIVED", "quantity": 10},
    )
    resp = admin_api.patch(f"/api/medicines/{created['id']}", json={"opening_stock": 500})
    assert resp.status_code == 422
    assert resp.json()["code"] == "opening_stock_locked"


def test_deactivate_medicine(admin_api):
    created = admin_api.post("/api/medicines", json=FIXTURE_MEDICINE).json()
    resp = admin_api.post(f"/api/medicines/{created['id']}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    # Second deactivate rejected
    resp = admin_api.post(f"/api/medicines/{created['id']}/deactivate")
    assert resp.status_code == 409


def test_duplicate_barcode_rejected(admin_api):
    admin_api.post("/api/medicines", json=FIXTURE_MEDICINE)
    other = {
        "name": "Other Med",
        "barcodes": [{"barcode": "8901234567890", "is_primary": True}],
    }
    resp = admin_api.post("/api/medicines", json=other)
    assert resp.status_code == 409
    assert resp.json()["code"] == "barcode_taken"
