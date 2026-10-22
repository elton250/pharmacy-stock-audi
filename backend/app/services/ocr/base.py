"""OCR engine protocol and shared types.

Any object implementing :class:`OcrEngine` can be plugged in (Tesseract today,
a different local engine tomorrow). The rest of the application never imports
a concrete engine, it only depends on this protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class OcrUnavailable(Exception):
    """Raised when no OCR engine (or its runtime) is available on this host."""


class OcrFailed(Exception):
    """Raised when the engine is available but failed to process the image."""


@dataclass
class OcrOutcome:
    """Result of one OCR run."""

    text: str = ""
    confidence: float | None = None
    engine: str = "unknown"
    words: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@runtime_checkable
class OcrEngine(Protocol):
    """Minimal interface every OCR implementation has to provide."""

    name: str

    def is_available(self) -> bool:
        """Whether the engine can currently run on this host."""

    def run(self, image_path: str) -> OcrOutcome:
        """Run OCR on an image file and return the recognised text."""
