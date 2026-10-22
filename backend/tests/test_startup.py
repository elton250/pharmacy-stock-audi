"""Startup tooling and health endpoint (the 502-after-reboot failure mode)."""
import os
import stat
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
RUNTIME_DIR = PROJECT_DIR / "runtime"


def test_health_endpoint_reports_database_status(api):
    resp = api.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"


def test_health_endpoint_reports_database_failure(raw_client, monkeypatch):
    """A dead database must produce 503 with a clear reason, not a silent 200."""
    from app.database import engine

    def _broken_connect(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(engine, "connect", _broken_connect)
    resp = raw_client.get("/api/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["database"] == "down"
    assert "detail" in body


@pytest.mark.parametrize("script", ["start-dev.sh", "check-health.sh"])
def test_startup_scripts_exist_and_are_executable(script):
    path = SCRIPTS_DIR / script
    assert path.is_file(), f"missing startup script: {path}"
    assert os.stat(path).st_mode & stat.S_IXUSR, f"{script} is not executable"


@pytest.mark.parametrize("script", ["start-dev.sh", "check-health.sh"])
def test_startup_scripts_do_not_use_tmp(script):
    """Runtime artefacts must stay inside the project (no /tmp usage)."""
    text = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
    assert "/tmp" not in text, f"{script} references /tmp"


def test_startup_script_starts_postgres_before_backend():
    text = (SCRIPTS_DIR / "start-dev.sh").read_text(encoding="utf-8")
    pg = text.index("start_postgres")
    backend = text.index("start_backend")
    frontend = text.index("start_frontend")
    assert pg < backend < frontend
    # The backend must wait for a healthy /api/health, and refuse to start
    # without a database (that was the 502 root cause).
    assert "backend_ready" in text
    assert "api/health" in text


def test_startup_script_uses_project_local_runtime_paths():
    text = (SCRIPTS_DIR / "start-dev.sh").read_text(encoding="utf-8")
    assert 'RUNTIME_DIR="$ROOT_DIR/runtime"' in text
    assert "PG_SOCKET_DIR" in text
    assert "PG_LOG" in text
    assert "BACKEND_LOG" in text
    assert "FRONTEND_LOG" in text


def test_runtime_directory_is_inside_the_project():
    assert RUNTIME_DIR.is_dir()
    assert str(RUNTIME_DIR).startswith(str(PROJECT_DIR))
