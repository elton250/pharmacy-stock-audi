"""Application configuration loaded from environment variables."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_capture_dir() -> Path:
    """Runtime directory for captured images.

    It lives in the project's ``runtime/`` directory, which is never served
    statically by the backend or the Vite dev server (only ``/api`` is
    proxied), so a capture can not be fetched by URL.
    """
    return Path(__file__).resolve().parents[2] / "runtime" / "captures"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Pharmacy Stock Auditing System"
    environment: str = "development"

    # Database (real credentials come from the environment / backend/.env)
    database_url: str = "postgresql+psycopg://pharmacy:CHANGE_ME@127.0.0.1:54329/pharmacy"
    database_url_test: str = "postgresql+psycopg://pharmacy:CHANGE_ME@127.0.0.1:54329/pharmacy_test"

    # Security (the real key comes from the environment / backend/.env)
    secret_key: str = "CHANGE_ME_GENERATE_WITH_secrets_token_urlsafe_48"
    session_cookie_name: str = "pharmacy_session"
    csrf_cookie_name: str = "pharmacy_csrf"
    session_ttl_hours: int = 12
    cookie_secure: bool = False
    cookie_same_site: str = "lax"

    # Login brute-force protection (per-process in Phase 1)
    login_max_failures: int = 5
    login_window_minutes: int = 15

    # Local display timezone (data is stored in UTC)
    local_timezone: str = "Africa/Dar_es_Salaam"

    # Counting rules
    max_package_chain_depth: int = 5

    # CORS (frontend dev server)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------------------------------------------------------- vision (Phase 2A)
    # Vision-assisted identification: a camera frame is OCR'd locally and the
    # recognised text is turned into *candidates* only. Nothing is ever
    # created or written to stock automatically — a human always confirms.
    vision_enabled: bool = True
    vision_capture_dir: Path = _default_capture_dir()
    vision_max_upload_bytes: int = 5 * 1024 * 1024
    vision_max_pixels: int = 25_000_000
    vision_retention_days: int = 30
    vision_candidate_limit: int = 10

    # OCR engine: local Tesseract only. No cloud OCR service is used.
    ocr_command: str = "tesseract"
    ocr_tessdata_dir: str = ""
    ocr_language: str = "eng"
    ocr_timeout_seconds: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
