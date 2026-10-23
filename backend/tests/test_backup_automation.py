"""Offline tests for the backup automation shell scripts.

These tests deliberately do not import tests/conftest.py, do not connect to
PostgreSQL, and do not create migrations or dumps of real data. They use fake
pg_dump/pg_restore binaries and temporary directories only.

Run from backend/:
    python -m unittest tests.test_backup_automation -v
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "scripts" / "backup-db.sh"
NIGHTLY = ROOT / "scripts" / "nightly-backup.sh"
VERIFY = ROOT / "scripts" / "verify-backup.sh"
HEALTH = ROOT / "scripts" / "check-health.sh"

FAKE_PG_DUMP = """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  echo 'pg_dump (PostgreSQL) 18.6'
  exit 0
fi
out=""
for arg in "$@"; do
  case "$arg" in
    --file=*) out="${arg#--file=}" ;;
  esac
done
[[ -n "$out" ]] || { echo 'fake pg_dump: no --file' >&2; exit 9; }
if [[ "${FAKE_PG_DUMP_FAIL:-0}" == "1" ]]; then
  echo 'fake pg_dump failure' >&2
  exit 1
fi
head -c "${FAKE_PG_DUMP_SIZE:-2048}" /dev/zero | tr '\\0' 'x' > "$out"
"""

FAKE_PG_RESTORE = """#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${FAKE_PG_RESTORE_CALLED:-}" ]]; then
  printf 'called\\n' >> "$FAKE_PG_RESTORE_CALLED"
fi
if [[ "${1:-}" == "--list" ]]; then
  printf '; fake archive TOC\\n1; 0 0 TABLE public users postgres\\n'
  exit 0
fi
exit 0
"""

FAKE_PSQL_EXISTING = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PSQL_LOG"
if [[ "$*" == *"pg_get_userbyid"* ]]; then
  printf '%s\\n' "${FAKE_OWNER_INFO:-test_user|test_user}"
  exit 0
fi
if [[ "$*" == *"pg_class"* ]]; then
  printf '%s\\n' "${FAKE_EXISTING_OBJECTS:-1|0|0}"
  exit 0
fi
if [[ "$*" == *"pg_database"* ]]; then
  printf '1\\n'
  exit 0
fi
if [[ "$*" == *"alembic_version"* ]]; then
  printf 'b71f4a92c308\\n'
  exit 0
fi
if [[ "$*" == *"information_schema.tables"* ]]; then
  printf 'users=3\\n'
  exit 0
fi
exit 0
"""

FAKE_PSQL_EMPTY_EXISTING = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PSQL_LOG"
if [[ "$*" == *"pg_get_userbyid"* ]]; then
  printf '%s\\n' "${FAKE_OWNER_INFO:-test_user|test_user}"
  exit 0
fi
if [[ "$*" == *"pg_class"* ]]; then
  printf '%s\\n' "${FAKE_EXISTING_OBJECTS:-0|0|0}"
  exit 0
fi
if [[ "$*" == *"pg_database"* ]]; then
  printf '1\\n'
  exit 0
fi
if [[ "$*" == *"alembic_version"* ]]; then
  printf 'b71f4a92c308\\n'
  exit 0
fi
if [[ "$*" == *"information_schema.tables"* ]]; then
  printf 'users=3\\n'
  exit 0
fi
exit 0
"""

FAKE_BACKUP_OK = """#!/usr/bin/env bash
set -euo pipefail
dir="${PHARMACY_BACKUP_DIR:?}"
mkdir -p "$dir"
f="$dir/pharmacy_nightly_fake_$$.dump"
head -c 2048 /dev/zero | tr '\\0' 'x' > "$f"
hash="$(sha256sum "$f" | awk '{print $1}')"
printf '%s  %s\\n' "$hash" "$(basename "$f")" > "$f.sha256"
{
  printf 'database=pharmacy\\n'
  printf 'label=nightly\\n'
  printf 'file=%s\\n' "$(basename "$f")"
  printf 'size_bytes=%s\\n' "$(stat -c %s "$f")"
  printf 'sha256=%s\\n' "$hash"
} > "$f.manifest"
"""

FAKE_BACKUP_FAIL = """#!/usr/bin/env bash
echo 'fake backup failure' >&2
exit 3
"""

FAKE_PSQL = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PSQL_LOG"
if [[ "$*" == *"pg_database"* ]]; then
  exit 0
fi
if [[ "$*" == *"alembic_version"* ]]; then
  printf 'b71f4a92c308\\n'
  exit 0
fi
if [[ "$*" == *"information_schema.tables"* ]]; then
  printf 'users=3\\n'
  exit 0
fi
exit 0
"""

FAKE_PSQL_COMPARE = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PSQL_LOG"
db=""; prev=""
for arg in "$@"; do
  if [[ "$prev" == "-d" ]]; then db="$arg"; fi
  prev="$arg"
done
if [[ "$*" == *"pg_database"* ]]; then
  exit 0
fi
if [[ "$*" == *"alembic_version"* ]]; then
  printf 'b71f4a92c308\\n'
  exit 0
fi
if [[ "$*" == *"information_schema.tables"* ]]; then
  case "$db" in
    pharmacy)
      if [[ -n "${FAKE_SOURCE_COUNTS:-}" ]]; then printf '%s\\n' "$FAKE_SOURCE_COUNTS"; fi
      ;;
    pharmacy_restore_drill_*)
      if [[ -n "${FAKE_TARGET_COUNTS:-}" ]]; then printf '%s\\n' "$FAKE_TARGET_COUNTS"; fi
      ;;
  esac
fi
exit 0
"""

FAKE_DB_TOOL = """#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$FAKE_DB_LOG"
exit 0
"""

FAKE_CURL = """#!/usr/bin/env bash
printf '200'
"""

FAKE_PG_ISREADY = """#!/usr/bin/env bash
exit 0
"""


def write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BackupAutomationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        write_exe(self.bin / "pg_dump", FAKE_PG_DUMP)
        write_exe(self.bin / "pg_restore", FAKE_PG_RESTORE)
        self.backup_dir = self.tmp / "backups"
        self.env_file = self.tmp / "test.env"
        self.env_file.write_text(
            "DATABASE_URL=postgresql+psycopg://test_user:test_pass@127.0.0.1:5432/pharmacy\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def env(self, **overrides: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHARMACY_")}
        env.update(
            {
                "PHARMACY_PG_BIN": str(self.bin),
                "PHARMACY_BACKUP_DIR": str(self.backup_dir),
                "PHARMACY_ENV_FILE": str(self.env_file),
                "PHARMACY_LOG_TO_SYSLOG": "0",
            }
        )
        env.update(overrides)
        return env

    def _write_dump(self, path: Path, database: str) -> None:
        path.write_bytes(b"x" * 2048)
        digest = sha256(path)
        Path(f"{path}.sha256").write_text(f"{digest}  {path.name}\n")
        Path(f"{path}.manifest").write_text(
            f"database={database}\n"
            "label=nightly\n"
            f"file={path.name}\n"
            f"size_bytes={path.stat().st_size}\n"
            f"sha256={digest}\n"
        )

    def run_backup(self, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            ["bash", str(BACKUP), *args],
            capture_output=True,
            text=True,
            env=env or self.env(),
        )

    def run_health(self, status_dir: Path):
        write_exe(self.bin / "curl", FAKE_CURL)
        write_exe(self.bin / "pg_isready", FAKE_PG_ISREADY)
        env = self.env(
            PHARMACY_BACKUP_STATUS_DIR=str(status_dir),
            PATH=f"{self.bin}:{os.environ.get('PATH', '')}",
        )
        return subprocess.run(
            ["bash", str(HEALTH)],
            capture_output=True,
            text=True,
            env=env,
        )

    def dumps(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob("*.dump"))

    def prepare_restore_compare(self) -> tuple[Path, Path]:
        write_exe(self.bin / "psql", FAKE_PSQL_COMPARE)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        return self.tmp / "psql-compare.log", self.tmp / "db-compare.log"

    def test_successful_backup_writes_private_dump_sidecar_and_manifest(self) -> None:
        result = self.run_backup("--database", "pharmacy", "--label", "nightly")
        self.assertEqual(result.returncode, 0, result.stderr)

        dumps = self.dumps()
        self.assertEqual(len(dumps), 1)
        dump = dumps[0]
        sidecar = Path(f"{dump}.sha256")
        manifest = Path(f"{dump}.manifest")

        self.assertEqual(stat.S_IMODE(self.backup_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(dump.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
        self.assertIn(sha256(dump), sidecar.read_text())
        self.assertIn(f"sha256={sha256(dump)}", manifest.read_text())
        self.assertIn("database=pharmacy", manifest.read_text())
        self.assertIn("label=nightly", manifest.read_text())

    def test_backup_timestamp_collision_does_not_overwrite_existing_backup(self) -> None:
        env = self.env(PHARMACY_BACKUP_STAMP="20200101_000000")
        first = self.run_backup("--database", "pharmacy", "--label", "nightly", env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        original = self.dumps()[0]
        original_bytes = original.read_bytes()
        original_sidecar = Path(f"{original}.sha256").read_bytes()
        original_manifest = Path(f"{original}.manifest").read_bytes()

        second = self.run_backup("--database", "pharmacy", "--label", "nightly", env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        dumps = self.dumps()
        self.assertEqual(len(dumps), 2, dumps)
        self.assertIn(original, dumps)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(Path(f"{original}.sha256").read_bytes(), original_sidecar)
        self.assertEqual(Path(f"{original}.manifest").read_bytes(), original_manifest)
        self.assertTrue(any(d.name.endswith("_1.dump") for d in dumps), dumps)

    def test_pg_dump_failure_leaves_no_dump_or_metadata(self) -> None:
        result = self.run_backup(
            "--database", "pharmacy", "--label", "nightly",
            env=self.env(FAKE_PG_DUMP_FAIL="1"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pg_dump failed", result.stderr)
        self.assertEqual(self.dumps(), [])
        self.assertEqual(list(self.backup_dir.glob("*.sha256")), [])
        self.assertEqual(list(self.backup_dir.glob("*.manifest")), [])
        self.assertEqual(list(self.backup_dir.glob(".incomplete_*")), [])

    def test_empty_dump_is_rejected(self) -> None:
        result = self.run_backup(
            "--database", "pharmacy", "--label", "nightly",
            env=self.env(FAKE_PG_DUMP_SIZE="0"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EMPTY", result.stderr)
        self.assertEqual(self.dumps(), [])

    def test_suspiciously_small_dump_is_rejected(self) -> None:
        result = self.run_backup(
            "--database", "pharmacy", "--label", "nightly",
            env=self.env(FAKE_PG_DUMP_SIZE="100"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("suspiciously small", result.stderr)
        self.assertEqual(self.dumps(), [])

    def test_retention_is_scoped_to_database_and_label(self) -> None:
        old_time = time.time() - 40 * 86400
        old_nightly = self.backup_dir / "pharmacy_nightly_20200101_000000.dump"
        old_evidence = self.backup_dir / "pharmacy_pre_fix_20200101_000000.dump"
        old_other_label = self.backup_dir / "pharmacy_test_nightly_20200101_000000.dump"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        for path in (old_nightly, old_evidence, old_other_label):
            path.write_bytes(b"old")
            path.with_name(path.name + ".sha256").write_text("old\n")
            path.with_name(path.name + ".manifest").write_text("old\n")
            os.utime(path, (old_time, old_time))

        result = self.run_backup(
            "--database", "pharmacy", "--label", "nightly",
            "--retention-days", "30", "--keep", "0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertFalse(old_nightly.exists())
        self.assertFalse(Path(f"{old_nightly}.sha256").exists())
        self.assertFalse(Path(f"{old_nightly}.manifest").exists())
        self.assertTrue(old_evidence.exists())
        self.assertTrue(old_other_label.exists())

    def test_verify_rejects_checksum_mismatch_before_touching_database(self) -> None:
        dump = self.tmp / "pharmacy_nightly_x.dump"
        dump.write_bytes(b"x" * 2048)
        sidecar = Path(f"{dump}.sha256")
        sidecar.write_text(f"{'0' * 64}  {dump.name}\n")
        manifest = Path(f"{dump}.manifest")
        manifest.write_text(
            f"file={dump.name}\nsize_bytes={dump.stat().st_size}\nsha256={'0' * 64}\n"
        )
        marker = self.tmp / "pg_restore_called"

        result = subprocess.run(
            ["bash", str(VERIFY), "--dump", str(dump), "--check-only"],
            capture_output=True,
            text=True,
            env=self.env(FAKE_PG_RESTORE_CALLED=str(marker)),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(marker.exists())

    def test_verify_check_only_accepts_valid_dump(self) -> None:
        dump = self.tmp / "pharmacy_nightly_x.dump"
        dump.write_bytes(b"x" * 2048)
        digest = sha256(dump)
        Path(f"{dump}.sha256").write_text(f"{digest}  {dump.name}\n")
        Path(f"{dump}.manifest").write_text(
            f"file={dump.name}\nsize_bytes={dump.stat().st_size}\nsha256={digest}\n"
        )
        marker = self.tmp / "pg_restore_called"

        result = subprocess.run(
            ["bash", str(VERIFY), "--dump", str(dump), "--check-only"],
            capture_output=True,
            text=True,
            env=self.env(FAKE_PG_RESTORE_CALLED=str(marker)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists())

    def test_verify_rejects_manifest_size_mismatch(self) -> None:
        dump = self.tmp / "pharmacy_nightly_x.dump"
        dump.write_bytes(b"x" * 2048)
        digest = sha256(dump)
        Path(f"{dump}.sha256").write_text(f"{digest}  {dump.name}\n")
        Path(f"{dump}.manifest").write_text(
            f"file={dump.name}\nsize_bytes=1\nsha256={digest}\n"
        )
        result = subprocess.run(
            ["bash", str(VERIFY), "--dump", str(dump), "--check-only"],
            capture_output=True,
            text=True,
            env=self.env(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest size", result.stderr)

    def test_verify_rejects_incomplete_manifest(self) -> None:
        dump = self.tmp / "pharmacy_nightly_incomplete_manifest.dump"
        dump.write_bytes(b"x" * 2048)
        digest = sha256(dump)
        Path(f"{dump}.sha256").write_text(f"{digest}  {dump.name}\n")
        Path(f"{dump}.manifest").write_text(f"file={dump.name}\n")
        result = subprocess.run(
            ["bash", str(VERIFY), "--dump", str(dump), "--check-only"],
            capture_output=True,
            text=True,
            env=self.env(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest sha256 does not match", result.stderr)

    def test_nightly_dry_run_touches_nothing(self) -> None:
        result = subprocess.run(
            ["bash", str(NIGHTLY), "--dry-run"],
            capture_output=True,
            text=True,
            env=self.env(PHARMACY_NIGHTLY_DIR=str(self.backup_dir)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.backup_dir.exists())
        self.assertIn("dry run", result.stdout)

    def test_nightly_failure_writes_status_and_calls_alert_hook(self) -> None:
        failing = self.tmp / "fake-backup-fail.sh"
        write_exe(failing, FAKE_BACKUP_FAIL)
        marker = self.tmp / "alert.txt"
        alert = self.tmp / "alert.sh"
        write_exe(alert, f"#!/usr/bin/env bash\nprintf '%s\\n' \"$1\" > '{marker}'\n")
        status_dir = self.backup_dir / "status"
        status_dir.mkdir(parents=True)
        (status_dir / "last-alert-failure").write_text("old alert failure\n")

        result = subprocess.run(
            ["bash", str(NIGHTLY), "--database", "pharmacy", "--label", "nightly"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_NIGHTLY_DIR=str(self.backup_dir),
                PHARMACY_BACKUP_SCRIPT=str(failing),
                PHARMACY_ALERT_CMD=str(alert),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        failure = self.backup_dir / "status" / "last-failure"
        self.assertTrue(failure.exists())
        self.assertIn("stage=backup", failure.read_text())
        self.assertTrue(marker.exists())
        self.assertIn("FAILED", marker.read_text())
        self.assertFalse((self.backup_dir / "status" / "last-success").exists())
        self.assertFalse((self.backup_dir / "status" / "last-alert-failure").exists())

    def test_nightly_alert_failure_does_not_change_successful_backup_status(self) -> None:
        ok = self.tmp / "fake-backup-ok.sh"
        write_exe(ok, FAKE_BACKUP_OK)
        alert = self.tmp / "alert-fail.sh"
        write_exe(alert, "#!/usr/bin/env bash\nexit 7\n")

        result = subprocess.run(
            ["bash", str(NIGHTLY), "--database", "pharmacy", "--label", "nightly"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_NIGHTLY_DIR=str(self.backup_dir),
                PHARMACY_BACKUP_SCRIPT=str(ok),
                PHARMACY_ALERT_CMD=str(alert),
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        success = self.backup_dir / "status" / "last-success"
        self.assertTrue(success.exists())
        self.assertIn("status=success", success.read_text())
        self.assertFalse((self.backup_dir / "status" / "last-failure").exists())
        alert_failure = self.backup_dir / "status" / "last-alert-failure"
        self.assertTrue(alert_failure.exists())
        text = alert_failure.read_text()
        self.assertIn("status=alert-failure", text)
        self.assertIn("backup_status=success", text)
        self.assertIn("alert_rc=7", text)
        self.assertNotIn("test_pass", text)

    def test_nightly_alert_failure_is_recorded_for_failed_backup(self) -> None:
        failing = self.tmp / "fake-backup-fail.sh"
        write_exe(failing, FAKE_BACKUP_FAIL)
        alert = self.tmp / "alert-fail.sh"
        write_exe(alert, "#!/usr/bin/env bash\nexit 9\n")

        result = subprocess.run(
            ["bash", str(NIGHTLY), "--database", "pharmacy", "--label", "nightly"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_NIGHTLY_DIR=str(self.backup_dir),
                PHARMACY_BACKUP_SCRIPT=str(failing),
                PHARMACY_ALERT_CMD=str(alert),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        failure = self.backup_dir / "status" / "last-failure"
        self.assertTrue(failure.exists())
        self.assertIn("stage=backup", failure.read_text())
        alert_failure = self.backup_dir / "status" / "last-alert-failure"
        self.assertTrue(alert_failure.exists())
        text = alert_failure.read_text()
        self.assertIn("backup_status=failure", text)
        self.assertIn("alert_rc=9", text)

    def test_health_backup_success_only_is_green(self) -> None:
        status_dir = self.tmp / "status"
        status_dir.mkdir()
        (status_dir / "last-success").write_text("status=success\n")
        result = self.run_health(status_dir)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[ OK ] Nightly backup", result.stdout)

    def test_health_failure_after_success_is_down(self) -> None:
        status_dir = self.tmp / "status"
        status_dir.mkdir()
        success = status_dir / "last-success"
        failure = status_dir / "last-failure"
        success.write_text("status=success\n")
        failure.write_text("status=failure\n")
        past = time.time() - 3600
        os.utime(success, (past, past))
        os.utime(failure, (time.time(), time.time()))
        result = self.run_health(status_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failure newer than last success", result.stdout)
        self.assertNotIn("[ OK ] Nightly backup", result.stdout)

    def test_health_success_after_failure_is_green(self) -> None:
        status_dir = self.tmp / "status"
        status_dir.mkdir()
        success = status_dir / "last-success"
        failure = status_dir / "last-failure"
        failure.write_text("status=failure\n")
        success.write_text("status=success\n")
        past = time.time() - 3600
        os.utime(failure, (past, past))
        os.utime(success, (time.time(), time.time()))
        result = self.run_health(status_dir)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[ OK ] Nightly backup", result.stdout)
        self.assertNotIn("failure newer than last success", result.stdout)

    def test_health_alert_failure_is_down(self) -> None:
        status_dir = self.tmp / "status"
        status_dir.mkdir()
        (status_dir / "last-success").write_text("status=success\n")
        (status_dir / "last-alert-failure").write_text("status=alert-failure\n")
        result = self.run_health(status_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nightly alert delivery", result.stdout)

    def test_nightly_success_verifies_and_writes_last_success(self) -> None:
        ok = self.tmp / "fake-backup-ok.sh"
        write_exe(ok, FAKE_BACKUP_OK)

        result = subprocess.run(
            ["bash", str(NIGHTLY), "--database", "pharmacy", "--label", "nightly"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_NIGHTLY_DIR=str(self.backup_dir),
                PHARMACY_BACKUP_SCRIPT=str(ok),
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        status = self.backup_dir / "status" / "last-success"
        self.assertTrue(status.exists())
        self.assertIn("status=success", status.read_text())
        self.assertIn("file=pharmacy_nightly_fake_", status.read_text())
        self.assertEqual(stat.S_IMODE(status.stat().st_mode), 0o600)
        self.assertFalse((self.backup_dir / "status" / "last-failure").exists())

    def test_nightly_dir_only_isolates_dump_and_metadata(self) -> None:
        nightly_dir = self.tmp / "nightly"
        log_dir = self.tmp / "nightly-logs"
        status_dir = self.tmp / "nightly-status"
        label = f"r7iso{os.getpid()}"
        repo_backups = ROOT / "backups"
        before = {p.name for p in repo_backups.iterdir()} if repo_backups.exists() else set()
        env = self.env(
            PHARMACY_NIGHTLY_DIR=str(nightly_dir),
            PHARMACY_BACKUP_LOG_DIR=str(log_dir),
            PHARMACY_BACKUP_STATUS_DIR=str(status_dir),
        )
        env.pop("PHARMACY_BACKUP_DIR", None)
        try:
            result = subprocess.run(
                ["bash", str(NIGHTLY), "--database", "pharmacy_test", "--label", label],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            dumps = sorted(nightly_dir.glob(f"pharmacy_test_{label}_*.dump"))
            self.assertEqual(len(dumps), 1, result.stderr)
            dump = dumps[0]
            self.assertTrue(Path(f"{dump}.sha256").exists())
            self.assertTrue(Path(f"{dump}.manifest").exists())
            self.assertIn("database=pharmacy_test", Path(f"{dump}.manifest").read_text())
            after = {p.name for p in repo_backups.iterdir()} if repo_backups.exists() else set()
            self.assertEqual(after - before, set(), "nightly wrote outside PHARMACY_NIGHTLY_DIR")
            self.assertTrue((status_dir / "last-success").exists())
        finally:
            for path in repo_backups.glob(f"pharmacy_test_{label}_*"):
                path.unlink()
            for path in repo_backups.glob(f".incomplete_pharmacy_test_{label}_*"):
                path.unlink()

    def test_nightly_rejects_bad_sidecar_from_backup_script(self) -> None:
        bad = self.tmp / "fake-backup-bad-checksum.sh"
        write_exe(
            bad,
            FAKE_BACKUP_OK.replace(
                'printf \'%s  %s\\n\' "$hash" "$(basename "$f")" > "$f.sha256"',
                'printf \'%064d  %s\\n\' 0 "$(basename "$f")" > "$f.sha256"',
            ),
        )
        result = subprocess.run(
            ["bash", str(NIGHTLY), "--database", "pharmacy", "--label", "nightly"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_NIGHTLY_DIR=str(self.backup_dir),
                PHARMACY_BACKUP_SCRIPT=str(bad),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.backup_dir / "status" / "last-failure").exists())

    def test_restore_no_live_compare_never_queries_source_database(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        psql_log = self.tmp / "psql.log"
        db_log = self.tmp / "db.log"
        dump = self.tmp / "pharmacy_nightly_x.dump"
        dump.write_bytes(b"x" * 2048)

        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "restore-db.sh"),
                "--drill", "--no-live-compare",
                "--expected-revision", "b71f4a92c308", str(dump),
            ],
            capture_output=True,
            text=True,
            env=self.env(FAKE_PSQL_LOG=str(psql_log), FAKE_DB_LOG=str(db_log)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESTORE VERIFICATION PASSED", result.stdout)
        self.assertIn("source database : pharmacy not queried", result.stdout)

        calls = psql_log.read_text().splitlines()
        self.assertTrue(calls)
        self.assertFalse(
            any(re.search(r"-d pharmacy(\s|$)", line) for line in calls),
            calls,
        )
        self.assertIn("dropdb", db_log.read_text())

    def test_restore_defaults_to_no_live_compare(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_default_no_live.dump"
        dump.write_bytes(b"x" * 2048)

        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "restore-db.sh"), "--drill", str(dump)],
            capture_output=True,
            text=True,
            env=self.env(
                FAKE_PSQL_LOG=str(psql_log),
                FAKE_DB_LOG=str(db_log),
                FAKE_SOURCE_COUNTS="users=5\n",
                FAKE_TARGET_COUNTS="users=5\n",
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source database : pharmacy not queried (default --no-live-compare", result.stdout)
        self.assertNotIn("row-count comparison", result.stdout)
        calls = psql_log.read_text().splitlines()
        self.assertFalse(
            any(re.search(r"-d pharmacy(\s|$)", line) for line in calls),
            calls,
        )
        self.assertIn("dropdb", db_log.read_text())

    def test_compare_source_requires_explicit_approval(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_needs_approval.dump"
        dump.write_bytes(b"x" * 2048)

        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "restore-db.sh"), "--drill", "--compare-source", str(dump)],
            capture_output=True,
            text=True,
            env=self.env(FAKE_PSQL_LOG=str(psql_log), FAKE_DB_LOG=str(db_log)),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires PHARMACY_ALLOW_SOURCE_COMPARE=1", result.stderr)
        self.assertFalse(psql_log.exists())
        self.assertFalse(db_log.exists())

    def test_restore_compare_key_tables_match_and_drop_temp_database(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_compare.dump"
        dump.write_bytes(b"x" * 2048)
        Path(f"{dump}.manifest").write_text("database=pharmacy\n")
        counts = (
            "users=5\nmedicines=7\ninventory_transactions=9\n"
            "stock_audits=3\naudit_logs=11\n"
        )

        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "restore-db.sh"),
                "--drill", "--compare-source",
                "--key-tables", "users,medicines,audit_logs", str(dump),
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_ALLOW_SOURCE_COMPARE="1",
                FAKE_PSQL_LOG=str(psql_log),
                FAKE_DB_LOG=str(db_log),
                FAKE_SOURCE_COUNTS=counts,
                FAKE_TARGET_COUNTS=counts,
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dump manifest database matches source: pharmacy", result.stdout)
        self.assertIn("OK    users", result.stdout)
        self.assertIn("RESTORE DRILL PASSED", result.stdout)
        self.assertIn("dropdb", db_log.read_text())

    def test_restore_compare_mismatch_still_drops_temp_database(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_mismatch.dump"
        dump.write_bytes(b"x" * 2048)

        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "restore-db.sh"),
                "--drill", "--compare-source", "--key-tables", "users", str(dump),
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_ALLOW_SOURCE_COMPARE="1",
                FAKE_PSQL_LOG=str(psql_log),
                FAKE_DB_LOG=str(db_log),
                FAKE_SOURCE_COUNTS="users=5\n",
                FAKE_TARGET_COUNTS="users=4\n",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DIFF", result.stdout)
        self.assertIn("RESTORE DRILL FAILED", result.stdout)
        self.assertIn("dropdb", db_log.read_text())

    def test_restore_compare_detects_target_only_table(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_target_only.dump"
        dump.write_bytes(b"x" * 2048)

        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "restore-db.sh"),
                "--drill", "--compare-source", str(dump),
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_ALLOW_SOURCE_COMPARE="1",
                FAKE_PSQL_LOG=str(psql_log),
                FAKE_DB_LOG=str(db_log),
                FAKE_SOURCE_COUNTS="users=5\n",
                FAKE_TARGET_COUNTS="users=5\nextra_target_only=1\n",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("extra_target_only", result.stdout)
        self.assertIn("source=<missing>", result.stdout)
        self.assertIn("RESTORE DRILL FAILED", result.stdout)
        self.assertIn("dropdb", db_log.read_text())

    def test_restore_refuses_dump_from_different_database_before_creating_temp_db(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_wrong_source.dump"
        dump.write_bytes(b"x" * 2048)
        Path(f"{dump}.manifest").write_text("database=pharmacy_test\n")

        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "restore-db.sh"),
                "--drill", "--no-live-compare", str(dump),
            ],
            capture_output=True,
            text=True,
            env=self.env(FAKE_PSQL_LOG=str(psql_log), FAKE_DB_LOG=str(db_log)),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to compare mismatched databases", result.stderr)
        self.assertFalse(psql_log.exists())
        self.assertFalse(db_log.exists())

    def test_restore_compare_fails_when_source_counts_unavailable(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_unreadable_source.dump"
        dump.write_bytes(b"x" * 2048)

        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "restore-db.sh"),
                "--drill", "--compare-source", "--key-tables", "users", str(dump),
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_ALLOW_SOURCE_COMPARE="1",
                FAKE_PSQL_LOG=str(psql_log),
                FAKE_DB_LOG=str(db_log),
                FAKE_SOURCE_COUNTS="",
                FAKE_TARGET_COUNTS="users=5\n",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not read the source database", result.stderr)
        self.assertIn("dropdb", db_log.read_text())

    def test_restore_compare_missing_key_table_fails_and_drops_temp_database(self) -> None:
        psql_log, db_log = self.prepare_restore_compare()
        dump = self.tmp / "pharmacy_nightly_missing_key.dump"
        dump.write_bytes(b"x" * 2048)

        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "restore-db.sh"),
                "--drill", "--compare-source",
                "--key-tables", "users,medicines", str(dump),
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_ALLOW_SOURCE_COMPARE="1",
                FAKE_PSQL_LOG=str(psql_log),
                FAKE_DB_LOG=str(db_log),
                FAKE_SOURCE_COUNTS="users=5\n",
                FAKE_TARGET_COUNTS="users=5\nmedicines=7\n",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("key table 'medicines' missing from source database", result.stderr)
        self.assertIn("dropdb", db_log.read_text())

    def test_restore_cleanup_failure_produces_nonzero_when_restore_was_ok(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(
            self.bin / "dropdb",
            "#!/usr/bin/env bash\necho 'drop failed' >&2\nexit 1\n",
        )
        dump = self.tmp / "pharmacy_nightly_cleanup.dump"
        self._write_dump(dump, "pharmacy")
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/restore-db.sh"), "--drill", str(dump), "--no-live-compare"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-cleanup.log"),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cleanup failed", result.stderr)

    def test_restore_cleanup_failure_preserves_prior_restore_error(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(
            self.bin / "dropdb",
            "#!/usr/bin/env bash\necho 'drop failed' >&2\nexit 1\n",
        )
        dump = self.tmp / "pharmacy_nightly_cleanup_prior.dump"
        self._write_dump(dump, "pharmacy")
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts/restore-db.sh"),
                "--drill",
                str(dump),
                "--expected-revision",
                "badrev",
                "--no-live-compare",
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-cleanup-prior.log"),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alembic revision mismatch", result.stderr)
        self.assertIn("preserving original failure", result.stderr)

    def test_restore_refuses_existing_target_without_allow(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL_EXISTING)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        dump = self.tmp / "pharmacy_nightly_existing.dump"
        self._write_dump(dump, "pharmacy")
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/restore-db.sh"), "--drill", str(dump), "--no-live-compare"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-existing.log"),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)

    def test_restore_allow_existing_requires_keep(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL_EMPTY_EXISTING)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        dump = self.tmp / "pharmacy_nightly_existing_requires_keep.dump"
        self._write_dump(dump, "pharmacy")
        psql_log = self.tmp / "psql-existing-no-keep.log"
        db_log = self.tmp / "db-existing-no-keep.log"
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "restore-db.sh"),
                "--drill",
                str(dump),
                "--allow-existing",
                "--no-live-compare",
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(psql_log),
                FAKE_DB_LOG=str(db_log),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--allow-existing requires --keep", result.stderr)
        self.assertFalse(psql_log.exists())
        self.assertFalse(db_log.exists())

    def test_restore_allow_existing_refuses_database_with_objects(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL_EXISTING)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        dump = self.tmp / "pharmacy_nightly_existing_data.dump"
        self._write_dump(dump, "pharmacy")
        db_log = self.tmp / "db-existing-data.log"
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "restore-db.sh"),
                "--drill",
                str(dump),
                "--allow-existing",
                "--keep",
                "--no-live-compare",
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-existing-data.log"),
                FAKE_DB_LOG=str(db_log),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains unexpected objects (relations=1", result.stderr)
        self.assertFalse(db_log.exists())

    def test_restore_allow_existing_refuses_other_owner(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL_EMPTY_EXISTING)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        dump = self.tmp / "pharmacy_nightly_existing_other_owner.dump"
        self._write_dump(dump, "pharmacy")
        db_log = self.tmp / "db-existing-other-owner.log"
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "restore-db.sh"),
                "--drill",
                str(dump),
                "--allow-existing",
                "--keep",
                "--no-live-compare",
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-existing-other-owner.log"),
                FAKE_DB_LOG=str(db_log),
                FAKE_OWNER_INFO="test_user|other_owner",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owned by 'other_owner', not the current role 'test_user'", result.stderr)
        self.assertFalse(db_log.exists())

    def test_restore_allow_existing_refuses_extra_schema(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL_EMPTY_EXISTING)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        dump = self.tmp / "pharmacy_nightly_existing_extra_schema.dump"
        self._write_dump(dump, "pharmacy")
        db_log = self.tmp / "db-existing-extra-schema.log"
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "restore-db.sh"),
                "--drill",
                str(dump),
                "--allow-existing",
                "--keep",
                "--no-live-compare",
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-existing-extra-schema.log"),
                FAKE_DB_LOG=str(db_log),
                FAKE_EXISTING_OBJECTS="0|1|0",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("extra_schemas=1", result.stderr)
        self.assertFalse(db_log.exists())

    def test_restore_allow_existing_accepts_empty_target_and_keeps_it(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL_EMPTY_EXISTING)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        dump = self.tmp / "pharmacy_nightly_existing_empty.dump"
        self._write_dump(dump, "pharmacy")
        db_log = self.tmp / "db-existing-empty.log"
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "restore-db.sh"),
                "--drill",
                str(dump),
                "--allow-existing",
                "--keep",
                "--no-live-compare",
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-existing-empty.log"),
                FAKE_DB_LOG=str(db_log),
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("owned by the current role and empty", result.stdout)
        self.assertIn("RESTORE VERIFICATION PASSED", result.stdout)
        self.assertFalse(db_log.exists())
        self.assertIn("kept disposable database", result.stdout)

    def test_restore_allow_existing_failure_does_not_drop_pre_existing_target(self) -> None:
        write_exe(self.bin / "psql", FAKE_PSQL_EMPTY_EXISTING)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        dump = self.tmp / "pharmacy_nightly_existing_failure.dump"
        self._write_dump(dump, "pharmacy")
        db_log = self.tmp / "db-existing-failure.log"
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "restore-db.sh"),
                "--drill",
                str(dump),
                "--allow-existing",
                "--keep",
                "--expected-revision",
                "badrev",
                "--no-live-compare",
            ],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-existing-failure.log"),
                FAKE_DB_LOG=str(db_log),
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alembic revision mismatch", result.stderr)
        self.assertFalse(db_log.exists())

    def test_nightly_verifies_new_dump_not_old_dump_with_newer_mtime(self) -> None:
        old = self.backup_dir / "pharmacy_nightly_zzz.dump"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._write_dump(old, "pharmacy")
        future = time.time() + 3600
        os.utime(old, (future, future))
        env = self.env(PHARMACY_NIGHTLY_DIR=str(self.backup_dir))
        env.pop("PHARMACY_BACKUP_DIR", None)
        result = subprocess.run(
            ["bash", str(NIGHTLY), "--database", "pharmacy", "--label", "nightly"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        dumps = sorted(self.backup_dir.glob("pharmacy_nightly_*.dump"))
        new_dumps = [d for d in dumps if d != old]
        self.assertEqual(len(new_dumps), 1)
        status = self.backup_dir / "status" / "last-success"
        self.assertTrue(status.exists())
        self.assertIn(f"file={new_dumps[0].name}", status.read_text())
        self.assertNotIn(f"file={old.name}", status.read_text())

    def test_verify_latest_nightly_uses_mtime_not_lexicographic_order(self) -> None:
        old = self.tmp / "pharmacy_nightly_zzz.dump"
        new = self.tmp / "pharmacy_nightly_aaa.dump"
        self._write_dump(old, "pharmacy")
        self._write_dump(new, "pharmacy")
        past = time.time() - 3600
        os.utime(old, (past, past))
        os.utime(new, (time.time(), time.time()))
        write_exe(self.bin / "psql", FAKE_PSQL)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        result = subprocess.run(
            ["bash", str(VERIFY), "--latest-nightly"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_NIGHTLY_DIR=str(self.tmp),
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-latest.log"),
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = result.stdout + result.stderr
        self.assertIn(new.name, combined)
        self.assertNotIn(old.name, combined)

    def test_verify_latest_nightly_skips_incomplete_newer_dump(self) -> None:
        eligible = self.tmp / "pharmacy_nightly_zzz.dump"
        incomplete = self.tmp / "pharmacy_nightly_aaa.dump"
        self._write_dump(eligible, "pharmacy")
        incomplete.write_bytes(b"x" * 2048)
        past = time.time() - 3600
        os.utime(eligible, (past, past))
        os.utime(incomplete, (time.time(), time.time()))
        write_exe(self.bin / "psql", FAKE_PSQL)
        write_exe(self.bin / "createdb", FAKE_DB_TOOL)
        write_exe(self.bin / "dropdb", FAKE_DB_TOOL)
        result = subprocess.run(
            ["bash", str(VERIFY), "--latest-nightly"],
            capture_output=True,
            text=True,
            env=self.env(
                PHARMACY_NIGHTLY_DIR=str(self.tmp),
                PHARMACY_PG_BIN=str(self.bin),
                FAKE_PSQL_LOG=str(self.tmp / "psql-latest-incomplete.log"),
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = result.stdout + result.stderr
        self.assertIn(eligible.name, combined)
        self.assertNotIn(incomplete.name, combined)
        self.assertIn("ignoring 1 dump(s) without .sha256/.manifest", combined)


if __name__ == "__main__":
    unittest.main()
