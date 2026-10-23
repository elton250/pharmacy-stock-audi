"""Operational-script tests: validated backup, restore drill, and the 0-byte regression.

These tests exercise the real shell scripts against the TEST database only.
The live `pharmacy` database is never dumped, restored, or modified here.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import TEST_DB_URL

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
BACKUP_SCRIPT = ROOT_DIR / "scripts" / "backup-db.sh"
RESTORE_SCRIPT = ROOT_DIR / "scripts" / "restore-db.sh"
PG_BIN = Path(os.environ.get("PHARMACY_PG_BIN", "/usr/lib/postgresql/18/bin"))

pytestmark = pytest.mark.skipif(
    not (PG_BIN / "pg_dump").exists() or not (PG_BIN / "pg_restore").exists(),
    reason="PostgreSQL client binaries (pg_dump/pg_restore) are required for the ops tests",
)


def _db_name(dsn: str) -> str:
    return dsn.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]


def _env_file(tmp_path: Path, dsn: str, port: str | None = None) -> Path:
    """Write a throwaway env file the ops scripts can read (PHARMACY_ENV_FILE)."""
    target = tmp_path / "env"
    if port:
        dsn = re.sub(r"@[^:/]+:\d+", f"@127.0.0.1:{port}", dsn)
    target.write_text(f"DATABASE_URL={dsn}\n")
    return target


def _run(script: Path, args: list[str], env_file: Path, backup_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PHARMACY_ENV_FILE"] = str(env_file)
    env["PHARMACY_BACKUP_DIR"] = str(backup_dir)
    env["PHARMACY_PG_BIN"] = str(PG_BIN)
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


def _list_databases() -> list[str]:
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DB_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT datname FROM pg_database")).scalars().all()
    engine.dispose()
    return list(rows)


# --------------------------------------------------------------------- scripts


def test_ops_scripts_exist_and_are_executable():
    for script in (BACKUP_SCRIPT, RESTORE_SCRIPT):
        assert script.is_file(), f"{script} is missing"
        assert os.access(script, os.X_OK), f"{script} is not executable"
        assert script.read_text().startswith("#!/usr/bin/env bash")


def test_backup_script_uses_a_temporary_file_and_validates():
    body = BACKUP_SCRIPT.read_text()
    # the 0-byte failure mode must be structurally impossible
    assert "--file=\"$TMPFILE\"" in body, "pg_dump must write to a temporary file"
    assert 'ln "$TMPFILE" "$candidate"' in body, "the dump must be published with an atomic no-clobber link"
    assert "TARGET_PUBLISHED=1" in body, "published dump ownership must be tracked for cleanup"
    assert 'mv -f "$TMPFILE" "$TARGET"' not in body, "publication must not overwrite an existing dump"
    assert "pg_dump failed" in body and "EMPTY file" in body
    assert "pg_restore --list" in body
    assert "stat -c %s" in body, "the dump size must be checked"


# --------------------------------------------------------------------- backup


def test_backup_creates_a_non_empty_verified_dump(tmp_path):
    backup_dir = tmp_path / "backups"
    env_file = _env_file(tmp_path, TEST_DB_URL)

    result = _run(
        BACKUP_SCRIPT,
        ["--database", _db_name(TEST_DB_URL), "--label", "pytest"],
        env_file,
        backup_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    dumps = sorted(backup_dir.glob("*.dump"))
    assert len(dumps) == 1, f"expected exactly one dump, got {dumps}"
    dump = dumps[0]
    assert dump.stat().st_size > 0, "the dump must not be empty"
    assert dump.stat().st_size > 1024, "the dump is suspiciously small"

    # the archive must be readable by pg_restore
    listed = subprocess.run(
        [str(PG_BIN / "pg_restore"), "--list", str(dump)],
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    assert "TABLE DATA" in listed.stdout


def test_backup_file_permissions_are_restrictive(tmp_path):
    backup_dir = tmp_path / "backups"
    env_file = _env_file(tmp_path, TEST_DB_URL)
    result = _run(
        BACKUP_SCRIPT,
        ["--database", _db_name(TEST_DB_URL), "--label", "perm"],
        env_file,
        backup_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    dump = next(backup_dir.glob("*.dump"))
    assert oct(dump.stat().st_mode)[-3:] == "600", "backup files must be 0600"
    assert oct(backup_dir.stat().st_mode)[-3:] == "700", "the backup dir must be 0700"


def test_failed_backup_leaves_no_file_behind(tmp_path):
    """Regression for the 0-byte dump: a failing pg_dump must not leave a 'backup'."""
    backup_dir = tmp_path / "backups"
    # port 1 is closed -> pg_dump cannot connect
    env_file = _env_file(tmp_path, TEST_DB_URL, port="1")

    result = _run(
        BACKUP_SCRIPT,
        ["--database", _db_name(TEST_DB_URL), "--label", "failcase"],
        env_file,
        backup_dir,
    )
    assert result.returncode != 0, "a failed backup must exit non-zero"
    assert "ERROR" in result.stdout + result.stderr

    assert not list(backup_dir.glob("*.dump")), "no dump file may be left behind"
    assert not list(backup_dir.glob(".incomplete_*")), "no partial file may be left behind"


def test_backup_reports_the_underlying_exit_status(tmp_path):
    backup_dir = tmp_path / "backups"
    env_file = _env_file(tmp_path, TEST_DB_URL, port="1")
    result = _run(BACKUP_SCRIPT, ["--database", _db_name(TEST_DB_URL)], env_file, backup_dir)
    combined = result.stdout + result.stderr
    assert "pg_dump failed (exit status" in combined
    assert "no backup was created" in combined


# -------------------------------------------------------------------- restore


def test_restore_requires_the_drill_flag(tmp_path):
    env_file = _env_file(tmp_path, TEST_DB_URL)
    result = _run(RESTORE_SCRIPT, ["some.dump"], env_file, tmp_path / "backups")
    assert result.returncode != 0
    assert "--drill is required" in result.stdout + result.stderr


def test_restore_refuses_an_empty_dump(tmp_path):
    env_file = _env_file(tmp_path, TEST_DB_URL)
    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    result = _run(RESTORE_SCRIPT, ["--drill", str(empty)], env_file, tmp_path / "backups")
    assert result.returncode != 0
    assert "empty" in (result.stdout + result.stderr).lower()


def test_restore_script_refuses_live_database_names():
    body = RESTORE_SCRIPT.read_text()
    assert 'TARGET_DB="pharmacy_restore_drill_' in body
    assert "refusing to use a live database name as the restore target" in body
    assert "refusing to restore over the live database" in body


def test_restore_drill_restores_verifies_and_cleans_up(tmp_path):
    backup_dir = tmp_path / "backups"
    env_file = _env_file(tmp_path, TEST_DB_URL)

    backup = _run(
        BACKUP_SCRIPT,
        ["--database", _db_name(TEST_DB_URL), "--label", "drill"],
        env_file,
        backup_dir,
    )
    assert backup.returncode == 0, backup.stdout + backup.stderr
    dump = next(backup_dir.glob("*.dump"))

    before = set(_list_databases())
    result = _run(RESTORE_SCRIPT, ["--drill", str(dump)], env_file, backup_dir)
    after = set(_list_databases())

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "RESTORE VERIFICATION PASSED" in combined
    assert "source data not queried" in combined
    # the disposable database must be gone again
    assert after == before, f"the drill database was not cleaned up: {after - before}"
    assert not any(db.startswith("pharmacy_restore_drill_") for db in after)


def test_restore_drill_reports_row_counts_for_every_table(tmp_path):
    backup_dir = tmp_path / "backups"
    env_file = _env_file(tmp_path, TEST_DB_URL)
    backup = _run(
        BACKUP_SCRIPT,
        ["--database", _db_name(TEST_DB_URL), "--label", "counts"],
        env_file,
        backup_dir,
    )
    assert backup.returncode == 0, backup.stdout + backup.stderr
    dump = next(backup_dir.glob("*.dump"))

    result = _run(RESTORE_SCRIPT, ["--drill", str(dump)], env_file, backup_dir)
    combined = result.stdout + result.stderr
    assert "users" in combined
    assert "medicines" in combined
    assert "DIFF" not in combined, combined


def test_backup_tooling_is_not_referenced_by_the_live_start_script():
    """The live start script must not silently run backups or migrations on boot."""
    start = (ROOT_DIR / "scripts" / "start-dev.sh").read_text()
    assert "pg_dump" not in start, "the start script must not take backups implicitly"
    assert "alembic upgrade head" in start, "migrations are still applied on start (documented limitation)"
    assert shutil.which("pg_dump") is not None
