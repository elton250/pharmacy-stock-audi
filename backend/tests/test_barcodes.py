"""Barcode-first identification and unknown-barcode handling."""
from tests.conftest import FIXTURE_MEDICINE


def test_scan_known_barcode(admin_api):
    admin_api.post("/api/medicines", json=FIXTURE_MEDICINE)
    resp = admin_api.get("/api/medicines/scan", params={"barcode": "8901234567890"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["medicine"]["name"] == "Paracetamol 500mg"


def test_scan_unknown_barcode(admin_api):
    resp = admin_api.get("/api/medicines/scan", params={"barcode": "0000000000000"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["medicine"] is None
    assert "Unknown barcode" in body["message"]


def test_unknown_barcode_is_never_assigned(admin_api):
    """Scanning an unknown barcode must not create or attach anything."""
    admin_api.post("/api/medicines", json=FIXTURE_MEDICINE)
    resp = admin_api.get("/api/medicines/scan", params={"barcode": "9999999999999"})
    assert resp.json()["found"] is False
    # Medicine unchanged: still exactly one barcode
    med = admin_api.get("/api/medicines/1").json()
    assert len(med["barcodes"]) == 1
    # And the unknown barcode still scans as unknown
    resp = admin_api.get("/api/medicines/scan", params={"barcode": "9999999999999"})
    assert resp.json()["found"] is False


def test_register_additional_barcode(admin_api):
    created = admin_api.post("/api/medicines", json=FIXTURE_MEDICINE).json()
    resp = admin_api.post(
        f"/api/medicines/{created['id']}/barcodes",
        json={"barcode": "1111222233334", "is_primary": False, "note": "supplier code"},
    )
    assert resp.status_code == 201
    # New barcode scans to same medicine
    resp = admin_api.get("/api/medicines/scan", params={"barcode": "1111222233334"})
    assert resp.json()["medicine"]["id"] == created["id"]


def test_primary_barcode_transfer(admin_api):
    created = admin_api.post("/api/medicines", json=FIXTURE_MEDICINE).json()
    resp = admin_api.post(
        f"/api/medicines/{created['id']}/barcodes",
        json={"barcode": "5555666677778", "is_primary": True},
    )
    assert resp.status_code == 201
    med = admin_api.get(f"/api/medicines/{created['id']}").json()
    primaries = [b for b in med["barcodes"] if b["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["barcode"] == "5555666677778"


def test_delete_barcode(admin_api):
    created = admin_api.post("/api/medicines", json=FIXTURE_MEDICINE).json()
    bc = created["barcodes"][0]
    resp = admin_api.delete(f"/api/medicines/barcodes/{bc['id']}")
    assert resp.status_code == 204
    resp = admin_api.get("/api/medicines/scan", params={"barcode": "8901234567890"})
    assert resp.json()["found"] is False


def test_barcode_manage_restricted_to_authorized(pharmacist_api):
    # pharmacist lacks barcode:manage
    resp = pharmacist_api.post(
        "/api/medicines/1/barcodes", json={"barcode": "1234567890123"}
    )
    assert resp.status_code == 403
