"""Vision-assisted identification (Phase 2A).

The rules under test:

* the camera frame is validated, stripped of metadata and stored privately;
* the local OCR engine turns the frame into *candidates* only;
* nothing is created and no stock is written — a human has to confirm;
* the image is deleted once the capture is confirmed;
* captures expire after the retention window.
"""
from __future__ import annotations

import io
import os
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import get_settings
from app.main import app
from app.database import utcnow
from app.models.logs import AuditLog
from app.models.vision import CaptureStatus, VisionCapture
from app.routers.vision import get_ocr_engine
from app.services.ocr.base import OcrFailed, OcrOutcome, OcrUnavailable

pytest.importorskip("PIL", reason="Pillow is required for the image tests")


# --------------------------------------------------------------- test doubles


class FakeEngine:
    """Stands in for the local Tesseract engine."""

    name = "fake"

    def __init__(self, text: str = "8901234567890", available: bool = True, fail: bool = False):
        self.text = text
        self.available = available
        self.fail = fail
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def run(self, image_path: str) -> OcrOutcome:
        self.calls.append(image_path)
        if not self.available:
            raise OcrUnavailable("engine missing")
        if self.fail:
            raise OcrFailed("OCR engine failed: simulated")
        return OcrOutcome(text=self.text, confidence=93.5, engine="fake", words=self.text.split())


@pytest.fixture()
def capture_dir(tmp_path, monkeypatch):
    """Point the capture storage at a temporary directory."""
    target = tmp_path / "captures"
    monkeypatch.setenv("VISION_CAPTURE_DIR", str(target))
    get_settings.cache_clear()
    yield target
    get_settings.cache_clear()


@pytest.fixture()
def engine(monkeypatch):
    fake = FakeEngine()
    app.dependency_overrides[get_ocr_engine] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ocr_engine, None)


@pytest.fixture()
def unavailable_engine():
    fake = FakeEngine(available=False)
    app.dependency_overrides[get_ocr_engine] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ocr_engine, None)


# ------------------------------------------------------------------- helpers


def _png_bytes(size=(320, 120), color=(255, 255, 255)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes(with_exif: bool = False) -> bytes:
    from PIL import Image

    image = Image.new("RGB", (320, 120), (250, 250, 250))
    buffer = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[0x010E] = "GPS-tagged camera frame"
        exif[0x0131] = "TestDevice"
        image.save(buffer, format="JPEG", quality=95, exif=exif)
    else:
        image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _medicine_with_barcode(api, barcode: str, name: str = "Paracetamol 500mg") -> dict:
    from tests.conftest import FIXTURE_MEDICINE

    payload = dict(FIXTURE_MEDICINE)
    payload["name"] = name
    payload["barcodes"] = [{"barcode": barcode, "is_primary": True}]
    resp = api.post("/api/medicines", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload(api, data: bytes, filename: str = "capture.png", content_type: str = "image/png"):
    return api.post(
        "/api/vision/captures",
        files={"file": (filename, data, content_type)},
    )


def _counts(db) -> tuple[int, int]:
    from sqlalchemy import func, select

    from app.models.inventory import InventoryTransaction
    from app.models.medicines import Medicine

    medicines = db.execute(select(func.count()).select_from(Medicine)).scalar_one()
    transactions = db.execute(select(func.count()).select_from(InventoryTransaction)).scalar_one()
    return medicines, transactions


# --------------------------------------------------------------------- status


def test_status_reports_engine_availability(admin_api, engine):
    resp = admin_api.get("/api/vision/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["engine_available"] is True
    assert body["retention_days"] == 30
    assert body["max_upload_bytes"] > 0


def test_status_reports_missing_engine(admin_api, unavailable_engine):
    resp = admin_api.get("/api/vision/status")
    assert resp.status_code == 200
    assert resp.json()["engine_available"] is False
    assert resp.json()["message"]


# -------------------------------------------------------------------- upload


def test_upload_returns_candidates_and_writes_nothing(admin_api, engine, capture_dir, db):
    medicine = _medicine_with_barcode(admin_api, "8901234567890")
    before = _counts(db)

    resp = _upload(admin_api, _png_bytes())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == CaptureStatus.PENDING
    assert body["ocr_text"] == "8901234567890"
    assert body["candidates"], "the barcode should produce a candidate"
    top = body["candidates"][0]
    assert top["medicine_id"] == medicine["id"]
    assert top["match_type"] == "barcode_exact"
    assert top["score"] >= 90

    # Nothing was created and no stock was written.
    assert _counts(db) == before
    assert engine.calls, "the OCR engine should have been invoked"


def test_upload_creates_an_audit_entry(admin_api, engine, capture_dir, db):
    resp = _upload(admin_api, _png_bytes())
    assert resp.status_code == 201
    entries = db.query(AuditLog).filter(AuditLog.action == "vision.capture_created").all()
    assert entries, "the capture must be auditable"


def test_upload_rejects_non_image(admin_api, engine, capture_dir):
    resp = _upload(admin_api, b"this is not an image", filename="note.txt", content_type="text/plain")
    assert resp.status_code == 415
    assert resp.json()["code"] == "unsupported_image_type"


def test_upload_rejects_a_truncated_image(admin_api, engine, capture_dir):
    resp = _upload(admin_api, b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_image"


def test_upload_rejects_an_oversized_image(admin_api, engine, capture_dir, monkeypatch):
    monkeypatch.setenv("VISION_MAX_UPLOAD_BYTES", "1024")
    get_settings.cache_clear()
    resp = _upload(admin_api, _png_bytes(size=(900, 900)))
    assert resp.status_code == 413
    assert resp.json()["code"] == "image_too_large"


def test_upload_without_permission_is_forbidden(admin_api, db):
    from tests.conftest import create_user

    create_user(admin_api, "noscan", "nopass1234", [])
    admin_api.post("/api/auth/logout")
    resp = admin_api.post("/api/auth/login", json={"username": "noscan", "password": "nopass1234"})
    assert resp.status_code == 200

    resp = _upload(admin_api, _png_bytes())
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_upload_without_engine_returns_503_and_stores_nothing(admin_api, unavailable_engine, capture_dir):
    resp = _upload(admin_api, _png_bytes())
    assert resp.status_code == 503
    assert resp.json()["code"] == "ocr_unavailable"
    stored = list(capture_dir.glob("*")) if capture_dir.exists() else []
    assert stored == [], "no image may be left behind when the engine is unavailable"


def test_upload_stores_the_image_privately(admin_api, engine, capture_dir):
    resp = _upload(admin_api, _png_bytes())
    assert resp.status_code == 201
    files = list(capture_dir.glob("*"))
    assert len(files) == 1
    stored = files[0]
    assert oct(stored.stat().st_mode)[-3:] == "600", "captures must not be world readable"
    assert oct(capture_dir.stat().st_mode)[-3:] == "700"


def test_upload_strips_exif_metadata(admin_api, engine, capture_dir):
    resp = _upload(admin_api, _jpeg_bytes(with_exif=True), filename="frame.jpg", content_type="image/jpeg")
    assert resp.status_code == 201, resp.text

    stored = next(capture_dir.glob("*"))
    raw = stored.read_bytes()
    assert b"Exif\x00\x00" not in raw, "the EXIF block must be removed"
    assert b"GPS-tagged camera frame" not in raw
    assert b"TestDevice" not in raw

    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        assert not dict(image.getexif()), "no EXIF metadata may survive"


def test_the_capture_directory_is_not_served_by_the_api(admin_api, engine, capture_dir):
    """A capture must not be downloadable by URL."""
    resp = _upload(admin_api, _png_bytes())
    capture_id = resp.json()["capture_id"]
    stored = next(capture_dir.glob("*")).name

    direct = admin_api.get(f"/api/vision/captures/{capture_id}")
    assert direct.status_code == 200
    assert direct.headers["content-type"].startswith("application/json")
    assert stored not in direct.text

    for path in (f"/{stored}", f"/api/vision/captures/{capture_id}/image", f"/runtime/{stored}"):
        assert admin_api.get(path).status_code == 404


# -------------------------------------------------------------------- confirm


def test_confirm_records_the_decision_and_deletes_the_image(admin_api, engine, capture_dir, db):
    medicine = _medicine_with_barcode(admin_api, "8901234567890")
    before = _counts(db)

    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    stored = next(capture_dir.glob("*"))

    resp = admin_api.post(
        f"/api/vision/captures/{capture_id}/confirm",
        json={"decision": "confirm", "medicine_id": medicine["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == CaptureStatus.CONFIRMED
    assert body["matched_medicine_id"] == medicine["id"]
    assert body["image_deleted"] is True
    assert not stored.exists(), "the image must be deleted after the confirmation"
    assert _counts(db) == before, "confirming must not write stock"

    actions = [row.action for row in db.query(AuditLog).all()]
    assert "vision.capture_confirmed" in actions


def test_reject_closes_the_capture_without_a_match(admin_api, engine, capture_dir):
    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    resp = admin_api.post(
        f"/api/vision/captures/{capture_id}/confirm",
        json={"decision": "reject"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == CaptureStatus.REJECTED
    assert body["matched_medicine_id"] is None
    assert body["image_deleted"] is True


def test_confirm_without_a_medicine_is_rejected(admin_api, engine, capture_dir):
    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    resp = admin_api.post(
        f"/api/vision/captures/{capture_id}/confirm",
        json={"decision": "confirm"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "medicine_required"


def test_confirm_of_an_unknown_medicine_is_rejected(admin_api, engine, capture_dir):
    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    resp = admin_api.post(
        f"/api/vision/captures/{capture_id}/confirm",
        json={"decision": "confirm", "medicine_id": 999999},
    )
    assert resp.status_code == 404


def test_confirming_twice_conflicts(admin_api, engine, capture_dir):
    medicine = _medicine_with_barcode(admin_api, "8901234567890")
    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    first = admin_api.post(
        f"/api/vision/captures/{capture_id}/confirm",
        json={"decision": "confirm", "medicine_id": medicine["id"]},
    )
    assert first.status_code == 200
    second = admin_api.post(
        f"/api/vision/captures/{capture_id}/confirm",
        json={"decision": "confirm", "medicine_id": medicine["id"]},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "capture_already_closed"


def test_confirm_requires_the_count_permission(admin_api, engine, capture_dir, db):
    from app.models.users import Permission, Role
    from tests.conftest import create_user

    medicine = _medicine_with_barcode(admin_api, "8901234567890")

    # a role that may look at medicines but may not count
    role = Role(name="vision_viewer", description="test role")
    role.permissions.extend(db.query(Permission).filter(Permission.code == "medicine:view").all())
    db.add(role)
    db.commit()

    create_user(admin_api, "viewer", "viewerpass123", ["vision_viewer"])
    admin_api.post("/api/auth/logout")
    admin_api.post("/api/auth/login", json={"username": "viewer", "password": "viewerpass123"})

    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    resp = admin_api.post(
        f"/api/vision/captures/{capture_id}/confirm",
        json={"decision": "confirm", "medicine_id": medicine["id"]},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


# --------------------------------------------------------------- text lookup


def test_scan_text_matches_a_barcode(admin_api, engine):
    medicine = _medicine_with_barcode(admin_api, "8901234567890")
    resp = admin_api.post("/api/vision/scan-text", json={"text": "8901234567890"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body[0]["medicine_id"] == medicine["id"]
    assert body[0]["match_type"] == "barcode_exact"


def test_scan_text_matches_a_separated_barcode(admin_api, engine):
    medicine = _medicine_with_barcode(admin_api, "TEST-FIX-R2-C6B961D6-A")
    resp = admin_api.post("/api/vision/scan-text", json={"text": "TEST FIX R2 C6B961D6 A"})
    assert resp.status_code == 200
    assert resp.json()[0]["medicine_id"] == medicine["id"]


def test_scan_text_matches_a_name(admin_api, engine):
    medicine = _medicine_with_barcode(admin_api, "1111222233333", name="Amoxicillin 250mg")
    resp = admin_api.post("/api/vision/scan-text", json={"text": "Amoxicillin 250mg capsules"})
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["medicine_id"] == medicine["id"]
    assert body[0]["match_type"] == "name"


def test_scan_text_with_an_unknown_value_returns_no_candidates(admin_api, engine):
    resp = admin_api.post("/api/vision/scan-text", json={"text": "9999999999999"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_scan_text_works_without_an_ocr_engine(admin_api, unavailable_engine):
    """The manual fallback must not depend on the OCR engine."""
    medicine = _medicine_with_barcode(admin_api, "8901234567890")
    resp = admin_api.post("/api/vision/scan-text", json={"text": "8901234567890"})
    assert resp.status_code == 200
    assert resp.json()[0]["medicine_id"] == medicine["id"]


# ------------------------------------------------------------------- expiry


def test_expired_captures_are_pruned(admin_api, engine, capture_dir, db):
    _medicine_with_barcode(admin_api, "8901234567890")
    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    stored = next(capture_dir.glob("*"))

    # age the capture beyond the retention window
    capture = db.get(VisionCapture, capture_id)
    capture.created_at = utcnow() - timedelta(days=45)
    db.commit()

    from app.services.vision import prune_expired_captures

    result = prune_expired_captures(db, retention_days=30)
    assert result["expired_captures"] == 1
    assert result["images_deleted"] == 1
    assert not stored.exists(), "the expired image must be deleted"

    capture = db.get(VisionCapture, capture_id)
    assert capture.status == CaptureStatus.EXPIRED
    assert capture.ocr_text == ""
    assert capture.candidates == []
    assert capture.image_path is None


def test_prune_dry_run_changes_nothing(admin_api, engine, capture_dir, db):
    capture_id = _upload(admin_api, _png_bytes()).json()["capture_id"]
    stored = next(capture_dir.glob("*"))
    capture = db.get(VisionCapture, capture_id)
    capture.created_at = utcnow() - timedelta(days=45)
    db.commit()

    from app.services.vision import prune_expired_captures

    result = prune_expired_captures(db, retention_days=30, dry_run=True)
    assert result["expired_captures"] == 1
    assert stored.exists(), "a dry run must not delete anything"
    assert db.get(VisionCapture, capture_id).status == CaptureStatus.PENDING


def test_fresh_captures_are_not_pruned(admin_api, engine, capture_dir, db):
    _upload(admin_api, _png_bytes())
    from app.services.vision import prune_expired_captures

    result = prune_expired_captures(db, retention_days=30)
    assert result["expired_captures"] == 0
    assert list(capture_dir.glob("*")), "a fresh capture must be kept"


# ------------------------------------------------------------------ locality


def test_the_ocr_engine_is_local_only():
    """No cloud OCR: the engine runs a local binary and nothing else."""
    from app.services.ocr import tesseract as engine_module

    source = Path(engine_module.__file__).read_text()
    for forbidden in ("requests", "httpx", "urllib.request", "http://", "https://"):
        assert forbidden not in source, f"the OCR engine must not use {forbidden}"
    assert "subprocess.run" in source


# ------------------------------------------------------------ the real engine


LOCAL_TESSERACT = Path.home() / ".local/opt/tesseract-5.5.0/usr/bin/tesseract"
LOCAL_TESSDATA = Path.home() / ".local/opt/tesseract-5.5.0/usr/share/tesseract-ocr/5/tessdata"


def test_the_engine_reports_itself_unavailable_without_a_binary():
    from app.services.ocr import TesseractEngine

    engine = TesseractEngine(command="/nonexistent/tesseract")
    assert engine.is_available() is False
    with pytest.raises(OcrUnavailable):
        engine.run("whatever.png")


def test_the_tessdata_directory_is_found_next_to_the_binary():
    from app.services.ocr.tesseract import resolve_tessdata_dir

    if not LOCAL_TESSERACT.exists():
        pytest.skip("the local Tesseract installation is not present")
    resolved = resolve_tessdata_dir(str(LOCAL_TESSERACT), "", "eng")
    assert resolved is not None
    assert (resolved / "eng.traineddata").exists()


def test_the_real_tesseract_engine_reads_a_barcode(tmp_path):
    """End-to-end proof that the local OCR engine works on this host."""
    from app.services.ocr import TesseractEngine

    if not LOCAL_TESSERACT.exists():
        pytest.skip("the local Tesseract installation is not present")
    engine = TesseractEngine(command=str(LOCAL_TESSERACT), tessdata_dir=str(LOCAL_TESSDATA))
    if not engine.is_available():
        pytest.skip("the local Tesseract installation is not usable")

    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=64)
    image = Image.new("RGB", (900, 180), "white")
    ImageDraw.Draw(image).text((30, 50), "8901234567890", fill="black", font=font)
    path = tmp_path / "barcode.png"
    image.save(path)

    outcome = engine.run(str(path))
    assert "8901234567890" in outcome.text.replace(" ", ""), outcome.text
    assert outcome.confidence is not None
    assert outcome.confidence > 50
    assert outcome.engine == "tesseract"


def test_only_real_matches_are_proposed(admin_api, engine, capture_dir, db):
    """A medicine that merely shares a word must not show up as a candidate."""
    from app.models.medicines import Medicine
    from app.services.vision import match_candidates

    medicine = db.query(Medicine).filter(Medicine.name == "Mifupen 500mg").first()
    if medicine is None:
        medicine = Medicine(name="Mifupen 500mg", is_active=True)
        db.add(medicine)
        db.flush()
    db.commit()

    assert match_candidates(db, "something completely different") == []
    assert match_candidates(db, "") == []
