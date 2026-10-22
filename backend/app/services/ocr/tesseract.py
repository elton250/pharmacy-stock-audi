"""Local Tesseract OCR engine.

The binary is executed directly (no Python wrapper dependency), which keeps
the deployment small and makes the engine easy to replace. Everything runs on
this host — no text ever leaves the machine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.services.ocr.base import OcrFailed, OcrOutcome, OcrUnavailable

# Directories that are searched for <lang>.traineddata when the setting is
# not configured explicitly.
_TESSDATA_CANDIDATES = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/local/share/tessdata",
    "/usr/share/tessdata",
)


def _candidate_tessdata_dirs(command: str) -> list[Path]:
    """Well-known tessdata locations, including one next to the binary itself."""
    dirs: list[Path] = []
    binary = shutil.which(command) or command
    try:
        binary_path = Path(binary).resolve()
    except OSError:  # pragma: no cover - defensive
        binary_path = Path(binary)
    # <prefix>/usr/bin/tesseract -> <prefix>/usr/share/tesseract-ocr/*/tessdata
    for share in binary_path.parents:
        if share.name == "usr":
            tess_root = share / "share" / "tesseract-ocr"
            if tess_root.is_dir():
                dirs.extend(sorted(tess_root.glob("*/tessdata")))
                dirs.append(tess_root / "tessdata")
            break
    dirs.extend(Path(d) for d in _TESSDATA_CANDIDATES)
    home = Path.home()
    dirs.append(home / ".local" / "share" / "tessdata")
    return dirs


def resolve_tessdata_dir(command: str, configured: str = "", language: str = "eng") -> Path | None:
    """Return a tessdata directory that contains the requested language."""
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_dir() else None
    for candidate in _candidate_tessdata_dirs(command):
        if candidate.is_dir() and (candidate / f"{language}.traineddata").exists():
            return candidate
    return None


def tesseract_available(command: str = "tesseract", configured_tessdata: str = "", language: str = "eng") -> bool:
    """Whether a usable Tesseract (binary + language data) exists on this host."""
    binary = shutil.which(command) or command
    if not Path(binary).exists() or not os.access(binary, os.X_OK):
        return False
    return resolve_tessdata_dir(command, configured_tessdata, language) is not None


class TesseractEngine:
    """OCR engine backed by the local ``tesseract`` binary."""

    name = "tesseract"

    def __init__(
        self,
        command: str = "tesseract",
        tessdata_dir: str = "",
        language: str = "eng",
        timeout_seconds: int = 15,
    ) -> None:
        self.command = command
        self.tessdata_dir = tessdata_dir
        self.language = language
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------ setup
    def binary_path(self) -> str:
        return shutil.which(self.command) or self.command

    def resolved_tessdata_dir(self) -> Path | None:
        return resolve_tessdata_dir(self.command, self.tessdata_dir, self.language)

    def is_available(self) -> bool:
        binary = self.binary_path()
        if not Path(binary).exists() or not os.access(binary, os.X_OK):
            return False
        return self.resolved_tessdata_dir() is not None

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        tessdata = self.resolved_tessdata_dir()
        if tessdata is not None:
            env["TESSDATA_PREFIX"] = str(tessdata)
        return env

    # -------------------------------------------------------------------- run
    def run(self, image_path: str) -> OcrOutcome:
        if not self.is_available():
            raise OcrUnavailable(
                "The local Tesseract OCR engine is not available on this host "
                "(binary or language data missing)."
            )
        # The TSV output is requested through the parameter instead of the
        # "tsv" config file, so the engine also works with a tessdata
        # directory that only contains the language data.
        cmd = [
            self.binary_path(),
            str(image_path),
            "stdout",
            "-l",
            self.language,
            "-c",
            "tessedit_create_tsv=1",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=self._env(),
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrFailed(f"OCR timed out after {self.timeout_seconds}s") from exc
        except OSError as exc:
            raise OcrUnavailable(f"Could not run the OCR engine: {exc}") from exc

        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            message = detail[-1] if detail else f"exit status {proc.returncode}"
            raise OcrFailed(f"OCR engine failed: {message}")

        return self._parse_tsv(proc.stdout)

    # ----------------------------------------------------------------- parsing
    @staticmethod
    def _parse_tsv(tsv: str) -> OcrOutcome:
        """Turn Tesseract's TSV output into text plus a mean word confidence."""
        lines: dict[tuple, list[str]] = {}
        order: list[tuple] = []
        confidences: list[float] = []
        for raw in tsv.splitlines():
            parts = raw.split("\t")
            if len(parts) < 12 or parts[0] != "5":
                continue
            word = parts[11].strip()
            if not word:
                continue
            try:
                conf = float(parts[10])
            except ValueError:
                conf = -1.0
            if conf >= 0:
                confidences.append(conf)
            key = (parts[1], parts[2], parts[3], parts[4])
            if key not in lines:
                lines[key] = []
                order.append(key)
            lines[key].append(word)

        text = "\n".join(" ".join(lines[key]) for key in order)
        mean_confidence = (sum(confidences) / len(confidences)) if confidences else None
        words = [w for key in order for w in lines[key]]
        return OcrOutcome(
            text=text,
            confidence=round(mean_confidence, 2) if mean_confidence is not None else None,
            engine=TesseractEngine.name,
            words=words,
        )
