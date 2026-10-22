"""OCR engine abstraction.

The application only ever talks to the small protocol defined in
:mod:`app.services.ocr.base`. The concrete implementation
(:mod:`app.services.ocr.tesseract`) runs a local Tesseract binary — no cloud
OCR service is used, and the engine can be replaced without touching the
routers or the database layer.
"""
from app.services.ocr.base import OcrEngine, OcrFailed, OcrOutcome, OcrUnavailable
from app.services.ocr.tesseract import TesseractEngine, tesseract_available

__all__ = [
    "OcrEngine",
    "OcrFailed",
    "OcrOutcome",
    "OcrUnavailable",
    "TesseractEngine",
    "tesseract_available",
]
