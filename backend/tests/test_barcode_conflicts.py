"""Duplicate-barcode handling: controlled conflicts, no unhandled IntegrityError.

Regression coverage for the case where a barcode that already belongs to the
same medicine was registered again. That used to reach the unique index and
surface as an unhandled IntegrityError (HTTP 500); it must be a clean 409 and
it must not change any barcode row.
"""
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from tests.conftest import FIXTURE_MEDICINE


def _medicine_with_barcode(api, barcode: str, name: str = "Paracetamol 500mg") -> dict:
    payload = dict(FIXTURE_MEDICINE)
    payload["name"] = name
    payload["barcodes"] = [{"barcode": barcode, "is_primary": True}]
    resp = api.post("/api/medicines", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _barcode_count(api, medicine_id: int) -> int:
    resp = api.get(f"/api/medicines/{medicine_id}")
    assert resp.status_code == 200, resp.text
    return len(resp.json()["barcodes"])


def test_same_medicine_duplicate_barcode_returns_409(admin_api):
    created = _medicine_with_barcode(admin_api, "8901234567890")
    before = _barcode_count(admin_api, created["id"])
    assert before == 1

    resp = admin_api.post(
        f"/api/medicines/{created['id']}/barcodes",
        json={"barcode": "8901234567890", "is_primary": False},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "barcode_already_exists"
    assert "8901234567890" in body["detail"]
    # The failed insert changed nothing.
    assert _barcode_count(admin_api, created["id"]) == before


def test_same_medicine_duplicate_keeps_primary_barcode_intact(admin_api):
    created = _medicine_with_barcode(admin_api, "8901234567890")
    resp = admin_api.post(
        f"/api/medicines/{created['id']}/barcodes",
        json={"barcode": "8901234567890", "is_primary": True},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "barcode_already_exists"
    med = admin_api.get(f"/api/medicines/{created['id']}").json()
    primaries = [b for b in med["barcodes"] if b["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["barcode"] == "8901234567890"


def test_cross_medicine_duplicate_barcode_still_returns_barcode_taken(admin_api):
    first = _medicine_with_barcode(admin_api, "8901234567890", name="Paracetamol 500mg")
    second = _medicine_with_barcode(admin_api, "1111222233333", name="Ibuprofen 400mg")

    resp = admin_api.post(
        f"/api/medicines/{second['id']}/barcodes",
        json={"barcode": "8901234567890", "is_primary": False},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "barcode_taken"
    # Neither medicine changed.
    assert _barcode_count(admin_api, first["id"]) == 1
    assert _barcode_count(admin_api, second["id"]) == 1


def test_duplicate_barcode_on_create_returns_409_and_creates_no_medicine(admin_api):
    _medicine_with_barcode(admin_api, "8901234567890")
    before = admin_api.get("/api/medicines").json()["total"]

    payload = dict(FIXTURE_MEDICINE)
    payload["name"] = "Duplicate Barcode Medicine"
    payload["barcodes"] = [{"barcode": "8901234567890", "is_primary": True}]
    resp = admin_api.post("/api/medicines", json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == "barcode_taken"
    # The rejected medicine was not persisted.
    assert admin_api.get("/api/medicines").json()["total"] == before


def test_duplicate_barcode_in_same_request_is_422(admin_api):
    created = _medicine_with_barcode(admin_api, "8901234567890")
    resp = admin_api.post(
        f"/api/medicines/{created['id']}/barcodes",
        json={"barcode": "2222333344445"},
    )
    assert resp.status_code == 201
    before = _barcode_count(admin_api, created["id"])

    payload = dict(FIXTURE_MEDICINE)
    payload["name"] = "Two Same Barcodes"
    payload["barcodes"] = [
        {"barcode": "3333444455556", "is_primary": False},
        {"barcode": "3333444455556", "is_primary": False},
    ]
    resp = admin_api.post("/api/medicines", json=payload)
    assert resp.status_code == 422
    assert resp.json()["code"] == "duplicate_barcode"
    assert _barcode_count(admin_api, created["id"]) == before


def test_barcode_uniqueness_race_is_reported_as_409(admin_api, monkeypatch):
    """A concurrent registration between the check and the INSERT is a conflict."""
    from app.models.medicines import MedicineBarcode
    from app.routers import medicines as medicines_router

    created = _medicine_with_barcode(admin_api, "8901234567890")
    before = _barcode_count(admin_api, created["id"])
    racing_code = "RACE-BARCODE-0001"

    original = medicines_router._validate_barcodes

    def racing_validate(db, medicine, barcodes):
        original(db, medicine, barcodes)
        for b in barcodes:
            if b.barcode.strip() == racing_code:
                # Another transaction wins the race and commits the row.
                db.add(
                    MedicineBarcode(
                        medicine_id=created["id"],
                        barcode=racing_code,
                        is_primary=False,
                        created_by=1,
                    )
                )
                db.flush()

    monkeypatch.setattr(medicines_router, "_validate_barcodes", racing_validate)
    resp = admin_api.post(
        f"/api/medicines/{created['id']}/barcodes",
        json={"barcode": racing_code, "is_primary": False},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "barcode_already_exists"
    # The losing transaction did not leave an extra row behind.
    assert _barcode_count(admin_api, created["id"]) == before


def test_only_the_barcode_constraint_is_treated_as_a_duplicate():
    from app.routers.medicines import _is_barcode_unique_violation

    barcode_conflict = IntegrityError(
        "INSERT ...",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(constraint_name="ix_medicine_barcodes_barcode"),
            sqlstate="23505",
        ),
    )
    assert _is_barcode_unique_violation(barcode_conflict) is True

    other_conflict = IntegrityError(
        "INSERT ...",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(constraint_name="uq_count_event_idempotency_key"),
            sqlstate="23505",
        ),
    )
    assert _is_barcode_unique_violation(other_conflict) is False

    other_error = IntegrityError(
        "INSERT ...",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(constraint_name=None),
            sqlstate="23503",
        ),
    )
    assert _is_barcode_unique_violation(other_error) is False
