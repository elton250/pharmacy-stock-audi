"""Medicine-specific package conversion and cut-blasters (partial packages)."""
from decimal import Decimal

from app.models.medicines import PackageConfiguration
from app.services.packages import base_units_for_line, package_units_map, validate_package_parent
from app.errors import ApiError
import pytest

from tests.conftest import FIXTURE_MEDICINE


def _create_med(admin_api, name: str, barcode: str, blister: int, box: int):
    resp = admin_api.post(
        "/api/medicines",
        json={
            "name": name,
            "base_unit": "tablet",
            "barcodes": [{"barcode": barcode, "is_primary": True}],
            "packages": [
                {"name": "Blister", "units_per_parent": blister, "sort_order": 1},
                {"name": "Box", "parent_name": "Blister", "units_per_parent": box, "sort_order": 2},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_different_medicines_have_independent_conversions(admin_api):
    """The system must not assume a universal package size."""
    med_a = _create_med(admin_api, "MedA", "1000000000001", blister=10, box=10)   # box = 100
    med_b = _create_med(admin_api, "MedB", "1000000000002", blister=12, box=5)    # box = 60

    pkg_a = {p["name"]: p for p in med_a["packages"]}
    pkg_b = {p["name"]: p for p in med_b["packages"]}
    assert Decimal(pkg_a["Box"]["units_in_base"]) == Decimal("100")
    assert Decimal(pkg_b["Box"]["units_in_base"]) == Decimal("60")


def test_cut_blister_example_236_tablets(db, admin_api):
    """Worked example: 2 boxes + 3 blisters + 1 cut blister (4 tabs) + 2 loose = 236 tablets."""
    med = _create_med(admin_api, "ExampleMed", "1000000000003", blister=10, box=10)
    by_id = package_units_map(db, med["id"])
    pkg_by_name = {p.name: p for p in by_id.values()}

    total = Decimal("0")
    # 2 full boxes
    base, partial = base_units_for_line(pkg_by_name["Box"], Decimal("2"), None, by_id)
    assert not partial
    total += base
    # 3 full blisters
    base, _ = base_units_for_line(pkg_by_name["Blister"], Decimal("3"), None, by_id)
    total += base
    # 1 cut blister containing 4 tablets
    base, partial = base_units_for_line(pkg_by_name["Blister"], Decimal("1"), Decimal("4"), by_id)
    assert partial
    total += base
    # 2 loose tablets
    base, _ = base_units_for_line(None, Decimal("2"), None, by_id)
    total += base

    assert total == Decimal("236")


def test_cut_box_counts_in_blisters(db, admin_api):
    """A cut box's partial_units are expressed in blisters (the child level)."""
    med = _create_med(admin_api, "ExampleMed2", "1000000000004", blister=10, box=10)
    by_id = package_units_map(db, med["id"])
    pkg_by_name = {p.name: p for p in by_id.values()}
    # Cut box with 4 blisters inside → 40 tablets
    base, partial = base_units_for_line(pkg_by_name["Box"], Decimal("1"), Decimal("4"), by_id)
    assert partial
    assert base == Decimal("40")


def test_package_cycle_rejected(db, admin_api):
    med = _create_med(admin_api, "CycleMed", "1000000000005", blister=10, box=10)
    packages = {p["name"]: p for p in med["packages"]}
    # Make Blister a child of Box while Box is a child of Blister → cycle
    resp = admin_api.put(
        f"/api/medicines/packages/{packages['Blister']['id']}",
        json={"parent_id": packages["Box"]["id"]},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "package_cycle"


def test_package_parent_must_belong_to_same_medicine(db, admin_api):
    med_a = _create_med(admin_api, "PmA", "1000000000006", blister=10, box=10)
    med_b = _create_med(admin_api, "PmB", "1000000000007", blister=5, box=4)
    box_a = next(p for p in med_a["packages"] if p["name"] == "Box")
    blister_b = next(p for p in med_b["packages"] if p["name"] == "Blister")
    with pytest.raises(ApiError):
        validate_package_parent(db, med_b["id"], blister_b["id"], 999999)
    # API-level: cannot point medicine B's package at medicine A's package
    resp = admin_api.put(
        f"/api/medicines/packages/{blister_b['id']}", json={"parent_id": box_a["id"]}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_parent"


def test_package_management_permissions(admin_api, pharmacist_api):
    # pharmacist lacks package:manage
    resp = pharmacist_api.post(
        "/api/medicines/1/packages", json={"name": "Bundle", "units_per_parent": 5}
    )
    assert resp.status_code == 403
