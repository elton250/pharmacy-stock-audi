#!/usr/bin/env bash
# Verify a backup without touching the live database.
#
#   scripts/verify-backup.sh --latest-nightly
#   scripts/verify-backup.sh --dump backups/nightly/pharmacy_nightly_....dump
#   scripts/verify-backup.sh --dump ... --check-only
#
# Checks performed:
#   * the .sha256 sidecar matches the dump byte-for-byte;
#   * the .manifest size, checksum and filename match the dump;
#   * pg_restore --list can read the archive;
#   * unless --check-only is given, the dump is restored into a disposable
#     database by restore-db.sh --no-live-compare and then dropped.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${PHARMACY_NIGHTLY_DIR:-$ROOT_DIR/backups/nightly}"
ENV_FILE="${PHARMACY_ENV_FILE:-$ROOT_DIR/backend/.env}"
PG_BIN="${PHARMACY_PG_BIN:-/usr/lib/postgresql/18/bin}"
PG_RESTORE="$PG_BIN/pg_restore"
RESTORE_SCRIPT="${PHARMACY_RESTORE_SCRIPT:-$ROOT_DIR/scripts/restore-db.sh}"
DATABASE="${PHARMACY_BACKUP_DATABASE:-pharmacy}"
LABEL="${PHARMACY_BACKUP_LABEL:-nightly}"
DUMP=""
CHECK_ONLY=0
KEEP=0
EXPECTED_REV=""

log()  { printf '[verify-backup] %s\n' "$*"; }
fail() { printf '[verify-backup] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: scripts/verify-backup.sh [--dump FILE | --latest-nightly] [options]
  --dump FILE            dump to verify
  --latest-nightly       pick the newest <database>_<label>_*.dump
  --env-file FILE        env file used for the disposable restore
  --expected-revision R  require this alembic revision in the restored copy
  --check-only           verify checksum/manifest/archive only; no DB access
  --keep                 keep the disposable database (restore mode only)
  -h, --help             show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dump) DUMP="$2"; shift 2 ;;
    --latest-nightly) DUMP="__LATEST__"; shift ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --expected-revision) EXPECTED_REV="$2"; shift 2 ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

if [[ "$DUMP" == "__LATEST__" ]]; then
  shopt -s nullglob
  CANDIDATES=( "$BACKUP_DIR/${DATABASE}_${LABEL}_"*.dump )
  (( ${#CANDIDATES[@]} > 0 )) ||
    fail "no ${DATABASE}_${LABEL}_*.dump found in $BACKUP_DIR"
  ELIGIBLE=()
  for candidate in "${CANDIDATES[@]}"; do
    if [[ -f "$candidate.sha256" && -f "$candidate.manifest" ]]; then
      ELIGIBLE+=("$candidate")
    fi
  done
  (( ${#ELIGIBLE[@]} > 0 )) ||
    fail "no eligible ${DATABASE}_${LABEL}_*.dump found in $BACKUP_DIR (missing .sha256/.manifest)"
  if (( ${#ELIGIBLE[@]} < ${#CANDIDATES[@]} )); then
    log "WARNING: ignoring $(( ${#CANDIDATES[@]} - ${#ELIGIBLE[@]} )) dump(s) without .sha256/.manifest"
  fi
  # Select newest by modification time (mtime), not lexicographic order.
  DUMP=""
  NEWEST_MTIME=-1
  for candidate in "${ELIGIBLE[@]}"; do
    candidate_mtime="$(stat -c %Y "$candidate" 2>/dev/null || echo 0)"
    if (( candidate_mtime > NEWEST_MTIME )); then
      NEWEST_MTIME="$candidate_mtime"
      DUMP="$candidate"
    fi
  done
  [[ -n "$DUMP" ]] || fail "could not determine the newest eligible backup in $BACKUP_DIR"
fi
[[ -n "$DUMP" ]] || { usage >&2; fail "--dump or --latest-nightly is required"; }
[[ -f "$DUMP" ]] || fail "dump not found: $DUMP"
[[ -s "$DUMP" ]] || fail "dump is empty: $DUMP"

DUMP_DIR="$(cd "$(dirname "$DUMP")" && pwd)"
DUMP_BASE="$(basename "$DUMP")"
DUMP="$DUMP_DIR/$DUMP_BASE"
SIDECAR="$DUMP.sha256"
MANIFEST="$DUMP.manifest"

[[ -f "$SIDECAR" ]] || fail "missing checksum sidecar: $SIDECAR"
[[ -f "$MANIFEST" ]] || fail "missing manifest: $MANIFEST"
[[ -x "$PG_RESTORE" ]] || fail "pg_restore not found at $PG_RESTORE (set PHARMACY_PG_BIN)"

log "verifying checksum: $SIDECAR"
if ! ( cd "$DUMP_DIR" && sha256sum -c "$DUMP_BASE.sha256" ); then
  fail "checksum mismatch for $DUMP_BASE"
fi

CHECKSUM="$(sha256sum "$DUMP" | awk '{print $1}')"
SIZE_BYTES="$(stat -c %s "$DUMP")"
MANIFEST_SHA="$(grep -E '^sha256=' "$MANIFEST" | tail -n 1 | cut -d= -f2- || true)"
MANIFEST_SIZE="$(grep -E '^size_bytes=' "$MANIFEST" | tail -n 1 | cut -d= -f2- || true)"
MANIFEST_FILE="$(grep -E '^file=' "$MANIFEST" | tail -n 1 | cut -d= -f2- || true)"
[[ "$MANIFEST_SHA" == "$CHECKSUM" ]] || fail "manifest sha256 does not match the dump"
[[ "$MANIFEST_SIZE" == "$SIZE_BYTES" ]] || fail "manifest size does not match the dump"
[[ "$MANIFEST_FILE" == "$DUMP_BASE" ]] || fail "manifest filename does not match the dump"
log "checksum and manifest match (${SIZE_BYTES} bytes, sha256 ${CHECKSUM})"

log "checking archive with pg_restore --list"
"$PG_RESTORE" --list "$DUMP" >/dev/null ||
  fail "pg_restore --list could not read the archive"

if (( CHECK_ONLY == 1 )); then
  log "VERIFY PASSED (check-only; no database contacted)"
  exit 0
fi

[[ -x "$RESTORE_SCRIPT" ]] || fail "restore script not executable: $RESTORE_SCRIPT"
ARGS=( --drill --no-live-compare )
if (( KEEP == 1 )); then ARGS+=( --keep ); fi
if [[ -n "$EXPECTED_REV" ]]; then ARGS+=( --expected-revision "$EXPECTED_REV" ); fi
ARGS+=( "$DUMP" )

log "restoring into a disposable database (source data is not queried)"
PHARMACY_ENV_FILE="$ENV_FILE" PHARMACY_PG_BIN="$PG_BIN" \
  "$RESTORE_SCRIPT" "${ARGS[@]}"
log "VERIFY PASSED"
