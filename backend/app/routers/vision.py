"""Vision-assisted identification API.

The flow is deliberately one-directional:

    camera frame  ->  local OCR  ->  candidates  ->  human confirmation

No endpoint in this router creates a medicine, writes stock or otherwise
changes the inventory. The only state changes are the capture record and its
audit trail.
"""
from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.audit import log_action
from app.config import get_settings
from app.database import get_db
from app.deps import require_permission
from app.errors import ApiError
from app.models.users import User
from app.models.vision import CaptureStatus, VisionCapture
from app.schemas.vision import (
    VisionCandidate,
    VisionCaptureOut,
    VisionConfirmIn,
    VisionStatusOut,
    VisionTextScanIn,
)
from app.services.ocr import OcrEngine, TesseractEngine
from app.services.blister_counting import count_blister_tablets
from app.services.vision import confirm_capture, create_capture, match_candidates

router = APIRouter(prefix="/api/vision", tags=["vision"])


def get_ocr_engine() -> OcrEngine:
    """The local OCR engine. Overridable in tests, replaceable in production."""
    settings = get_settings()
    return TesseractEngine(
        command=settings.ocr_command,
        tessdata_dir=settings.ocr_tessdata_dir,
        language=settings.ocr_language,
        timeout_seconds=settings.ocr_timeout_seconds,
    )


def _out(capture: VisionCapture, engine_available: bool = True) -> VisionCaptureOut:
    return VisionCaptureOut(
        capture_id=capture.id,
        status=capture.status,
        ocr_text=capture.ocr_text,
        ocr_confidence=float(capture.ocr_confidence) if capture.ocr_confidence is not None else None,
        engine=capture.engine,
        engine_available=engine_available,
        candidates=[VisionCandidate(**candidate) for candidate in (capture.candidates or [])],
        matched_medicine_id=capture.matched_medicine_id,
        created_at=capture.created_at,
        expires_at=capture.expires_at,
        confirmed_at=capture.confirmed_at,
        image_deleted=not capture.image_path,
    )


def _get_capture_or_404(db: DbSession, capture_id: str) -> VisionCapture:
    capture = db.get(VisionCapture, capture_id)
    if capture is None:
        raise ApiError(404, "Capture not found", "not_found")
    return capture


def _ensure_schema(db: DbSession) -> None:
    """Refuse to work when the migration for this feature is not applied yet.

    A deployment that starts the new code before the migration ran would
    otherwise answer with an opaque database error.
    """
    from sqlalchemy import text

    exists = db.execute(text("SELECT to_regclass('public.vision_captures') IS NOT NULL")).scalar()
    if not exists:
        raise ApiError(
            503,
            "The vision feature is not available yet: the database migration "
            "'c8f2a4b91d7e' has not been applied.",
            "migration_pending",
        )


@router.get("/status", response_model=VisionStatusOut)
def vision_status(
    _: User = Depends(require_permission("medicine:view")),
    engine: OcrEngine = Depends(get_ocr_engine),
):
    """Whether the local OCR engine can run on this host."""
    settings = get_settings()
    available = engine.is_available()
    return VisionStatusOut(
        enabled=settings.vision_enabled,
        engine=engine.name,
        engine_available=available,
        message=(
            None
            if available
            else "The local OCR engine is not available. Search manually instead."
        ),
        max_upload_bytes=settings.vision_max_upload_bytes,
        retention_days=settings.vision_retention_days,
    )


@router.post("/captures", response_model=VisionCaptureOut, status_code=201)
async def upload_capture(
    request: Request,
    file: UploadFile = File(...),
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("medicine:view")),
    engine: OcrEngine = Depends(get_ocr_engine),
):
    """Accept a camera frame, OCR it and return candidate medicines.

    The image is validated, stripped of metadata, stored in a non-public
    runtime directory and deleted automatically once the capture is confirmed.
    """
    _ensure_schema(db)
    settings = get_settings()
    limit = settings.vision_max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(
            413,
            f"The image is too large (limit {limit} bytes)",
            "image_too_large",
        )

    capture = create_capture(
        db,
        user,
        data=data,
        declared_type=file.content_type,
        engine=engine,
    )
    log_action(
        db,
        action="vision.capture_created",
        entity_type="vision_capture",
        entity_id=capture.id,
        actor=user,
        request=request,
        after={
            "status": capture.status,
            "engine": capture.engine,
            "candidates": len(capture.candidates or []),
            "size_bytes": capture.size_bytes,
        },
    )
    db.commit()
    return _out(capture, engine_available=engine.is_available())



@router.post("/captures/{capture_id}/count-blister")
def count_blister(
    capture_id: str,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:perform")),
):
    """Analyze a pending blister image and propose a visible tablet count.

    This endpoint never writes inventory. The capture owner must review and
    explicitly accept the result in the counting workflow.
    """
    _ensure_schema(db)
    capture = _get_capture_or_404(db, capture_id)
    if capture.user_id != user.id:
        raise ApiError(403, "Only the user who created this capture can analyze it", "forbidden")
    if capture.status != CaptureStatus.PENDING:
        raise ApiError(409, "Only a pending capture can be analyzed", "capture_already_closed")
    if not capture.image_path or not capture.content_type:
        raise ApiError(410, "The captured image is no longer available", "capture_image_unavailable")
    result = count_blister_tablets(capture.image_path, capture.content_type)
    log_action(
        db,
        action="vision.blister_count_proposed",
        entity_type="vision_capture",
        entity_id=capture.id,
        actor=user,
        after={
            "visible_tablet_count": result["visible_tablet_count"],
            "confidence": result["confidence"],
            "provider_model": result["provider_model"],
            "requires_human_confirmation": True,
        },
    )
    db.commit()
    return {"capture_id": capture.id, **result}

@router.get("/captures/{capture_id}", response_model=VisionCaptureOut)
def get_capture(
    capture_id: str,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("medicine:view")),
    engine: OcrEngine = Depends(get_ocr_engine),
):
    _ensure_schema(db)
    capture = _get_capture_or_404(db, capture_id)
    return _out(capture, engine_available=engine.is_available())


@router.post("/captures/{capture_id}/confirm", response_model=VisionCaptureOut)
def confirm(
    capture_id: str,
    payload: VisionConfirmIn,
    request: Request,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_permission("count:perform")),
):
    """Confirm (or reject) the identification a capture proposes.

    This records the human decision only — no stock is written.
    """
    _ensure_schema(db)
    # Serialize confirmations for this capture. Without a row lock, two
    # concurrent requests could both observe PENDING before either commits.
    capture = db.execute(
        select(VisionCapture).where(VisionCapture.id == capture_id).with_for_update()
    ).scalar_one_or_none()
    if capture is None:
        raise ApiError(404, "Capture not found", "not_found")
    if capture.user_id != user.id:
        raise ApiError(403, "Only the user who created this capture can confirm it", "forbidden")
    before = {"status": capture.status, "matched_medicine_id": capture.matched_medicine_id}

    updated = confirm_capture(
        db,
        capture,
        user,
        decision=payload.decision,
        medicine_id=payload.medicine_id,
    )

    candidate_ids = [c.get("medicine_id") for c in (capture.candidates or [])]
    log_action(
        db,
        action="vision.capture_confirmed" if payload.decision == "confirm" else "vision.capture_rejected",
        entity_type="vision_capture",
        entity_id=updated.id,
        actor=user,
        request=request,
        before=before,
        after={
            "status": updated.status,
            "matched_medicine_id": updated.matched_medicine_id,
            "note": payload.note,
            "was_candidate": updated.matched_medicine_id in candidate_ids if updated.matched_medicine_id else False,
        },
    )
    db.commit()
    return _out(updated)


@router.post("/scan-text", response_model=list[VisionCandidate])
def scan_text(
    payload: VisionTextScanIn,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_permission("medicine:view")),
):
    """Match typed text (barcode or name) against the catalogue.

    This is the manual fallback: it needs no OCR engine and no image.
    """
    settings = get_settings()
    candidates = match_candidates(db, payload.text, settings.vision_candidate_limit)
    return [VisionCandidate(**candidate) for candidate in candidates]
