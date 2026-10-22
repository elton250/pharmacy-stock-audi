"""Inventory transactions and expected-stock calculations."""
from decimal import Decimal

from tests.conftest import FIXTURE_MEDICINE


def _medicine(api, opening=0, name="StockMed"):
    resp = api.post(
        "/api/medicines", json=FIXTURE_MEDICINE | {"name": name, "opening_stock": opening}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_transaction_types_and_signs(admin_api):
    med = _medicine(admin_api, opening=100)
    mid = med["id"]
    steps = [
        ("RECEIVED", 50, 150),
        ("ISSUED", 30, 120),
        ("RETURN_IN", 5, 125),
        ("RETURN_OUT", 10, 115),
        ("ADJUSTMENT_POSITIVE", 7, 122),
        ("ADJUSTMENT_NEGATIVE", 2, 120),
    ]
    for ttype, qty, expected in steps:
        resp = admin_api.post(
            "/api/inventory/transactions",
            json={"medicine_id": mid, "transaction_type": ttype, "quantity": qty},
        )
        assert resp.status_code == 201, resp.text
        med_out = admin_api.get(f"/api/medicines/{mid}").json()
        assert Decimal(med_out["expected_stock"]) == Decimal(expected), f"after {ttype}"


def test_expected_stock_formula(admin_api):
    """Expected = Opening + Received + ReturnsIn + AdjPos − Issued − ReturnsOut − AdjNeg."""
    med = _medicine(admin_api, opening=200)
    mid = med["id"]
    for ttype, qty in [
        ("RECEIVED", 100),
        ("RETURN_IN", 10),
        ("ADJUSTMENT_POSITIVE", 5),
        ("ISSUED", 60),
        ("RETURN_OUT", 5),
        ("ADJUSTMENT_NEGATIVE", 10),
    ]:
        admin_api.post(
            "/api/inventory/transactions",
            json={"medicine_id": mid, "transaction_type": ttype, "quantity": qty},
        )
    expected = 200 + 100 + 10 + 5 - 60 - 5 - 10  # = 240
    med_out = admin_api.get(f"/api/medicines/{mid}").json()
    assert Decimal(med_out["expected_stock"]) == Decimal(expected)


def test_negative_quantity_rejected(admin_api):
    med = _medicine(admin_api)
    resp = admin_api.post(
        "/api/inventory/transactions",
        json={"medicine_id": med["id"], "transaction_type": "RECEIVED", "quantity": -5},
    )
    assert resp.status_code == 422


def test_opening_via_transaction_rejected(admin_api):
    med = _medicine(admin_api)
    resp = admin_api.post(
        "/api/inventory/transactions",
        json={"medicine_id": med["id"], "transaction_type": "OPENING", "quantity": 10},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "opening_via_transaction"


def test_transaction_records_user_and_reference(admin_api):
    med = _medicine(admin_api)
    resp = admin_api.post(
        "/api/inventory/transactions",
        json={
            "medicine_id": med["id"],
            "transaction_type": "RECEIVED",
            "quantity": 40,
            "reference_number": "GRN-001",
            "notes": "Delivery from supplier",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["reference_number"] == "GRN-001"
    assert body["recorded_by_username"] == "admin"
    assert Decimal(body["signed_quantity"]) == Decimal("40")


def test_transaction_list_filtering(admin_api):
    med = _medicine(admin_api, opening=100)
    admin_api.post(
        "/api/inventory/transactions",
        json={"medicine_id": med["id"], "transaction_type": "ISSUED", "quantity": 5},
    )
    resp = admin_api.get(
        "/api/inventory/transactions",
        params={"medicine_id": med["id"], "transaction_type": "ISSUED"},
    )
    assert resp.json()["total"] == 1
    resp = admin_api.get("/api/inventory/transactions", params={"medicine_id": med["id"]})
    assert resp.json()["total"] == 2  # opening + issued
