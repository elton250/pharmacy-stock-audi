"""Vision-assisted identification.

Responsibilities of this module:
* validate an uploaded image (size, real file type, pixel count);
* strip metadata (EXIF/GPS) by re-encoding the picture;
* store it in a non-public runtime directory with restrictive permissions;
* run the local OCR engine over it;
* turn the recognised text into *candidate* medicines.

Nothing here creates a medicine, writes stock, or decides anything: the result
is always a proposal that a human has to confirm.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.database import utcnow
from app.errors import ApiError
from app.models.medicines import Medicine, MedicineBarcode
from app.models.users import User
from app.models.vision import CaptureStatus, VisionCapture
from app.services.ocr import OcrFailed, OcrUnavailable

# The formats we accept. The declared content type is only a hint — the real
# type is detected from the file signature.
ALLOWED_FORMATS: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def detect_content_type(data: bytes) -> Optional[str]:
    """Detect the real image type from its magic bytes."""
    for signature, content_type in _MAGIC_SIGNATURES:
        if data.startswith(signature):
            return content_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def capture_dir() -> Path:
    """The non-public directory that holds captured images."""
    path = Path(get_settings().vision_capture_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - best effort
        pass
    return path


def normalize_code(value: str) -> str:
    """Normalise a barcode-like string: upper case, letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def extract_tokens(text: str) -> list[str]:
    """Split OCR text into word-like tokens."""
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_./]{2,}", text or "")


def validate_and_sanitize(data: bytes, declared_type: str | None) -> tuple[bytes, str]:
    """Check the upload and return sanitised bytes without any metadata.

    Raises:
        ApiError 413 when the file is too large or has too many pixels,
        ApiError 415 when the file is not a supported image,
        ApiError 422 when the file is not a decodable image,
        ApiError 503 when the optional imaging dependency is missing.
    """
    settings = get_settings()
    if not data:
        raise ApiError(422, "The uploaded file is empty", "empty_upload")
    if len(data) > settings.vision_max_upload_bytes:
        raise ApiError(
            413,
            f"The image is too large (limit {settings.vision_max_upload_bytes} bytes)",
            "image_too_large",
        )

    content_type = detect_content_type(data)
    if content_type is None or content_type not in ALLOWED_FORMATS:
        raise ApiError(
            415,
            "Only JPEG, PNG and WebP images are supported",
            "unsupported_image_type",
        )

    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ApiError(
            503,
            "Image processing is not available on this server (Pillow is not installed)",
            "image_support_unavailable",
        ) from exc

    try:
        import io

        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > settings.vision_max_pixels:
                raise ApiError(
                    413,
                    "The image resolution is too large to process",
                    "image_too_large",
                )
            # Apply the EXIF orientation and drop every metadata block.
            cleaned = ImageOps.exif_transpose(image)
            if cleaned.mode not in ("RGB", "RGBA"):
                cleaned = cleaned.convert("RGBA" if "A" in image.getbands() else "RGB")
            buffer = io.BytesIO()
            if content_type == "image/png":
                cleaned.save(buffer, format="PNG")
            elif content_type == "image/webp":
                cleaned.save(buffer, format="WEBP", quality=90)
            else:
                cleaned.save(buffer, format="JPEG", quality=90)
            sanitized = buffer.getvalue()
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(422, f"The image could not be decoded: {exc.__class__.__name__}", "invalid_image") from exc

    return sanitized, content_type


def store_image(capture_id: str, data: bytes) -> Path:
    """Write the sanitised image to the non-public capture directory."""
    path = capture_dir() / f"{capture_id}.bin"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def delete_image(capture: VisionCapture) -> bool:
    """Delete the stored image (if any). Returns True when a file was removed."""
    if not capture.image_path:
        return False
    path = Path(capture.image_path)
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - defensive
        return False
    capture.image_path = None
    return True


# --------------------------------------------------------------------- matching


def _load_pool(db: DbSession, tokens: Iterable[str], words: Iterable[str]) -> tuple[list, list]:
    """Load the barcodes and medicines that could possibly match the text."""
    codes = [normalize_code(t) for t in tokens]
    codes = [c for c in codes if len(c) >= 6]
    words = [w.lower() for w in words if len(w) >= 4][:12]

    barcode_stmt = select(MedicineBarcode)
    if codes:
        # ``translate`` strips the separators, so "TEST-FIX-123" also matches
        # the OCR text "TESTFIX123".
        norm_expr = func.translate(MedicineBarcode.barcode, "-_ ./", "")
        barcode_stmt = barcode_stmt.where(or_(*[norm_expr.ilike(f"%{code}%") for code in codes]))
    barcodes = list(db.execute(barcode_stmt.limit(2000)).scalars())

    medicine_stmt = select(Medicine)
    if words:
        conditions = []
        for word in words:
            pattern = f"%{word}%"
            conditions.extend(
                [
                    Medicine.name.ilike(pattern),
                    Medicine.generic_name.ilike(pattern),
                    Medicine.brand_name.ilike(pattern),
                    Medicine.strength.ilike(pattern),
                ]
            )
        medicine_stmt = medicine_stmt.where(or_(*conditions))
    medicines = list(db.execute(medicine_stmt.limit(2000)).scalars())
    return barcodes, medicines


def match_candidates(db: DbSession, text: str, limit: int = 10) -> list[dict]:
    """Turn OCR text into ranked medicine candidates.

    Barcode matches win over name matches; the result is always a proposal.
    """
    text = text or ""
    tokens = extract_tokens(text)
    words = [t.lower() for t in tokens if len(t) >= 4 and not t.isdigit()]
    codes = {normalize_code(t) for t in tokens}
    full = normalize_code(text)
    if len(full) >= 6:
        codes.add(full)

    barcodes, medicines = _load_pool(db, list(codes), words)
    if not barcodes and not medicines:
        return []

    by_medicine: dict[int, dict] = {}

    def _entry(medicine: Medicine) -> dict:
        return by_medicine.setdefault(
            medicine.id,
            {
                "medicine_id": medicine.id,
                "name": medicine.name,
                "generic_name": medicine.generic_name,
                "brand_name": medicine.brand_name,
                "strength": medicine.strength,
                "barcode": None,
                "match_type": "name",
                "score": 0.0,
            },
        )

    # --- barcode matches -----------------------------------------------------
    for barcode_row in barcodes:
        medicine = db.get(Medicine, barcode_row.medicine_id)
        if medicine is None or not medicine.is_active:
            continue
        normalized = normalize_code(barcode_row.barcode)
        if not normalized:
            continue
        match_type = None
        score = 0.0
        if normalized in codes:
            match_type, score = "barcode_exact", 100.0
        elif any(len(code) >= 6 and (code in normalized or normalized in code) for code in codes):
            match_type, score = "barcode_partial", 75.0
        if match_type is None:
            continue
        entry = _entry(medicine)
        if score > entry["score"]:
            entry.update({"barcode": barcode_row.barcode, "match_type": match_type, "score": score})

    # --- name matches --------------------------------------------------------
    text_lower = text.lower()
    text_norm = normalize_code(text)
    for medicine in medicines:
        if not medicine.is_active:
            continue
        entry = _entry(medicine)
        haystacks = [
            medicine.name,
            medicine.generic_name or "",
            medicine.brand_name or "",
            medicine.strength or "",
        ]
        haystack_lower = " ".join(h for h in haystacks if h).lower()
        matched_words = {w for w in words if w in haystack_lower}
        name_score = 0.0
        name_type = "name"
        if medicine.name and normalize_code(medicine.name) and normalize_code(medicine.name) in text_norm:
            name_score = 90.0
        elif matched_words:
            name_score = 40.0 + 10.0 * len(matched_words)
        if name_score and name_score > entry["score"]:
            entry.update({"match_type": name_type, "score": name_score})

    # Only real matches are proposed: a medicine that merely shares a word
    # with the search text but does not match it is not a candidate.
    matched = [item for item in by_medicine.values() if item["score"] > 0]
    ranked = sorted(matched, key=lambda item: (-item["score"], item["name"]))
    return ranked[:limit]


# -------------------------------------------------------------------- captures


def create_capture(
    db: DbSession,
    user: User,
    *,
    data: bytes,
    declared_type: str | None,
    engine,
) -> VisionCapture:
    """Store an image, OCR it and return the capture with its candidates."""
    settings = get_settings()
    if not settings.vision_enabled:
        raise ApiError(503, "Vision-assisted identification is disabled", "vision_disabled")
    if not engine.is_available():
        raise ApiError(
            503,
            "The local OCR engine is not available on this server. Use the manual search instead.",
            "ocr_unavailable",
        )

    sanitized, content_type = validate_and_sanitize(data, declared_type)
    capture_id = uuid.uuid4().hex
    path = store_image(capture_id, sanitized)

    try:
        outcome = engine.run(str(path))
    except OcrUnavailable as exc:
        path.unlink(missing_ok=True)
        raise ApiError(
            503,
            "The local OCR engine is not available on this server. Use the manual search instead.",
            "ocr_unavailable",
        ) from exc
    except OcrFailed as exc:
        path.unlink(missing_ok=True)
        raise ApiError(422, str(exc), "ocr_failed") from exc

    now = utcnow()
    candidates = match_candidates(db, outcome.text, settings.vision_candidate_limit)
    capture = VisionCapture(
        id=capture_id,
        user_id=user.id,
        status=CaptureStatus.PENDING,
        image_path=str(path),
        image_sha256=hashlib.sha256(sanitized).hexdigest(),
        content_type=content_type,
        size_bytes=len(sanitized),
        ocr_text=outcome.text,
        ocr_confidence=outcome.confidence,
        engine=outcome.engine,
        candidates=candidates,
        expires_at=now + timedelta(days=settings.vision_retention_days),
    )
    db.add(capture)
    db.commit()
    return capture


def confirm_capture(
    db: DbSession,
    capture: VisionCapture,
    user: User,
    *,
    decision: str,
    medicine_id: int | None,
) -> VisionCapture:
    """Close a capture with a human decision. Never writes stock."""
    if capture.status != CaptureStatus.PENDING:
        raise ApiError(409, "This capture was already closed", "capture_already_closed")

    medicine = None
    if decision == "confirm":
        if medicine_id is None:
            raise ApiError(422, "A medicine has to be confirmed", "medicine_required")
        medicine = db.get(Medicine, medicine_id)
        if medicine is None:
            raise ApiError(404, "Medicine not found", "not_found")

    # Do not close the capture if its private image could not be removed.
    if capture.image_path and not delete_image(capture):
        raise ApiError(
            503,
            "The captured image could not be deleted; please retry",
            "image_delete_failed",
        )

    if decision == "confirm":
        capture.status = CaptureStatus.CONFIRMED
        capture.matched_medicine_id = medicine.id
    else:
        capture.status = CaptureStatus.REJECTED

    capture.confirmed_at = utcnow()
    db.add(capture)
    db.commit()
    return capture


def prune_expired_captures(
    db: DbSession,
    *,
    retention_days: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Delete images that are older than the retention window.

    The row is kept as a minimal trace (status ``expired``); the OCR text and
    the candidate list are cleared because they are no longer needed.
    """
    settings = get_settings()
    days = settings.vision_retention_days if retention_days is None else retention_days
    cutoff = utcnow() - timedelta(days=days)

    rows = list(
        db.execute(
            select(VisionCapture).where(VisionCapture.created_at < cutoff)
        ).scalars()
    )
    expired = [row for row in rows if row.status == CaptureStatus.PENDING]
    expired_count = 0
    deleted_files = 0
    for row in expired:
        if dry_run:
            expired_count += 1
            continue

        had_image = bool(row.image_path)
        image_existed = bool(
            row.image_path and Path(row.image_path).is_file()
        )
        if had_image and not delete_image(row):
            continue

        if image_existed:
            deleted_files += 1
        row.status = CaptureStatus.EXPIRED
        row.ocr_text = ""
        row.candidates = []
        db.add(row)
        expired_count += 1

    if not dry_run:
        db.commit()
    return {
        "retention_days": days,
        "cutoff": cutoff.isoformat(),
        "expired_captures": expired_count,
        "images_deleted": deleted_files,
        "dry_run": dry_run,
    }
