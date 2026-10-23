#!/usr/bin/env bash
# Nightly backup wrapper around scripts/backup-db.sh.
#
#   scripts/nightly-backup.sh --dry-run
#   PHARMACY_NIGHTLY_DIR=/tmp/nightly-test scripts/nightly-backup.sh --database pharmacy_test --label nightly-test
#
# The wrapper adds what a cron job needs and the backup engine deliberately
# does not:
#   * a non-blocking lock so two runs cannot overlap;
#   * private log/status directories (0700) and files (0600);
#   * checksum + manifest verification of the dump that was just written;
#   * last-success / last-failure status files for monitoring;
#   * last-alert-failure kept separate from the backup result;
#   * a logger and optional PHARMACY_ALERT_CMD hook.
#
# It never reads database contents itself. All access is delegated to
# backup-db.sh, which reads the DSN from the env file.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${PHARMACY_NIGHTLY_DIR:-${PHARMACY_BACKUP_DIR:-$ROOT_DIR/backups/nightly}}"
export PHARMACY_BACKUP_DIR="$BACKUP_DIR"
LOG_DIR="${PHARMACY_BACKUP_LOG_DIR:-$BACKUP_DIR/logs}"
STATUS_DIR="${PHARMACY_BACKUP_STATUS_DIR:-$BACKUP_DIR/status}"
ENV_FILE="${PHARMACY_ENV_FILE:-$ROOT_DIR/backend/.env}"
BACKUP_SCRIPT="${PHARMACY_BACKUP_SCRIPT:-$ROOT_DIR/scripts/backup-db.sh}"
DATABASE="${PHARMACY_BACKUP_DATABASE:-pharmacy}"
LABEL="${PHARMACY_BACKUP_LABEL:-nightly}"
RETENTION_DAYS="${PHARMACY_BACKUP_RETENTION_DAYS:-30}"
KEEP="${PHARMACY_BACKUP_KEEP:-10}"
ALERT_CMD="${PHARMACY_ALERT_CMD:-}"
LOG_TO_SYSLOG="${PHARMACY_LOG_TO_SYSLOG:-1}"
DRY_RUN=0
LOG_FILE=""
STAGE="init"

log()  { printf '[nightly-backup] %s\n' "$*"; }
fail() { printf '[nightly-backup] ERROR: %s\n' "$*" >&2; exit 1; }

record_alert_failure() {
  local context="$1" alert_rc="$2"
  mkdir -p "$STATUS_DIR" 2>/dev/null || true
  {
    printf 'status=alert-failure\n'
    printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'timestamp_epoch=%s\n' "$(date +%s)"
    printf 'backup_status=%s\n' "$context"
    printf 'stage=%s\n' "$STAGE"
    printf 'alert_rc=%s\n' "$alert_rc"
    printf 'log=%s\n' "${LOG_FILE:-<not-created>}"
  } > "$STATUS_DIR/.last-alert-failure.tmp" 2>/dev/null &&
    mv -f "$STATUS_DIR/.last-alert-failure.tmp" "$STATUS_DIR/last-alert-failure" || true
  chmod 600 "$STATUS_DIR/last-alert-failure" 2>/dev/null || true
  log "WARNING: alert delivery failed (exit ${alert_rc}); recorded in $STATUS_DIR/last-alert-failure"
}

notify() {
  local context="$1" message="$2" alert_rc=0
  if [[ "$LOG_TO_SYSLOG" == "1" ]] && command -v logger >/dev/null 2>&1; then
    logger -t pharmacy-backup "$message" || log "WARNING: syslog logger failed"
  fi
  if [[ -n "$ALERT_CMD" ]]; then
    if [[ -x "$ALERT_CMD" ]]; then
      "$ALERT_CMD" "$message" || alert_rc=$?
      if (( alert_rc != 0 )); then
        record_alert_failure "$context" "$alert_rc"
        return 1
      fi
      rm -f "$STATUS_DIR/last-alert-failure"
    else
      record_alert_failure "$context" 126
      return 1
    fi
  fi
  return 0
}

finish() {
  local rc=$?
  if (( rc == 0 )); then return 0; fi
  mkdir -p "$STATUS_DIR" 2>/dev/null || true
  {
    printf 'status=failure\n'
    printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'stage=%s\n' "$STAGE"
    printf 'exit_code=%s\n' "$rc"
    printf 'log=%s\n' "${LOG_FILE:-<not-created>}"
  } > "$STATUS_DIR/.last-failure.tmp" 2>/dev/null &&
    mv -f "$STATUS_DIR/.last-failure.tmp" "$STATUS_DIR/last-failure" || true
  chmod 600 "$STATUS_DIR/last-failure" 2>/dev/null || true
  notify "failure" "pharmacy-backup FAILED stage=$STAGE rc=$rc log=${LOG_FILE:-<not-created>}" || true
}

usage() {
  cat <<'EOF'
Usage: scripts/nightly-backup.sh [options]
  --database NAME     database to dump (default: $PHARMACY_BACKUP_DATABASE or pharmacy)
  --label NAME        filename label (default: nightly)
  --retention-days N  delete labeled dumps older than N days (default: 30)
  --keep N            always keep at least N newest labeled dumps (default: 10)
  --dry-run           print what would run; touch nothing
  -h, --help          show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --database) DATABASE="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

if (( DRY_RUN == 1 )); then
  printf '[nightly-backup] dry run: no files, dumps, locks, or database access\n'
  printf '  backup script : %s\n' "$BACKUP_SCRIPT"
  printf '  env file      : %s\n' "$ENV_FILE"
  printf '  backup dir    : %s\n' "$BACKUP_DIR"
  printf '  database      : %s (label %s)\n' "$DATABASE" "$LABEL"
  printf '  retention     : %s days, keep %s\n' "$RETENTION_DAYS" "$KEEP"
  printf '  command       : %s --database %s --label %s --retention-days %s --keep %s\n' \
    "$BACKUP_SCRIPT" "$DATABASE" "$LABEL" "$RETENTION_DAYS" "$KEEP"
  exit 0
fi

umask 077
mkdir -p "$BACKUP_DIR" "$LOG_DIR" "$STATUS_DIR"
chmod 700 "$BACKUP_DIR" "$LOG_DIR" "$STATUS_DIR" 2>/dev/null || true

LOG_FILE="$LOG_DIR/nightly-$(date -u +%Y%m).log"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"
if (( $(stat -c %s "$LOG_FILE") > 10485760 )); then
  mv -f "$LOG_FILE" "${LOG_FILE}.1"
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE"
fi

trap finish EXIT

STAGE="lock"
exec 9>"$BACKUP_DIR/.nightly.lock"
if ! flock -n 9; then
  fail "another nightly backup is already running (lock: $BACKUP_DIR/.nightly.lock)"
fi

START_EPOCH="$(date +%s)"
log "starting backup of '$DATABASE' (label $LABEL)"
STAGE="backup"
shopt -s nullglob
BEFORE_DUMPS=( "$BACKUP_DIR/${DATABASE}_${LABEL}_"*.dump )
if ! "$BACKUP_SCRIPT" --database "$DATABASE" --label "$LABEL" \
     --retention-days "$RETENTION_DAYS" --keep "$KEEP" 2>&1 | tee -a "$LOG_FILE"; then
  fail "backup-db.sh failed; see $LOG_FILE"
fi

STAGE="verify"
DUMPS=( "$BACKUP_DIR/${DATABASE}_${LABEL}_"*.dump )
if (( ${#DUMPS[@]} == 0 )); then
  fail "backup-db.sh returned success but produced no labeled dump in $BACKUP_DIR"
fi
NEW_DUMPS=()
for candidate in "${DUMPS[@]}"; do
  seen_before=0
  for old in "${BEFORE_DUMPS[@]}"; do
    if [[ "$candidate" == "$old" ]]; then
      seen_before=1
      break
    fi
  done
  if (( seen_before == 0 )); then
    NEW_DUMPS+=("$candidate")
  fi
done
if (( ${#NEW_DUMPS[@]} == 0 )); then
  fail "backup-db.sh returned success but produced no new labeled dump in $BACKUP_DIR"
fi
DUMP=""
NEWEST_MTIME=-1
for candidate in "${NEW_DUMPS[@]}"; do
  candidate_mtime="$(stat -c %Y "$candidate" 2>/dev/null || echo 0)"
  if (( candidate_mtime > NEWEST_MTIME )); then
    NEWEST_MTIME="$candidate_mtime"
    DUMP="$candidate"
  fi
done
[[ -n "$DUMP" ]] || fail "could not determine the new dump in $BACKUP_DIR"
SIDECAR="$DUMP.sha256"
MANIFEST="$DUMP.manifest"

[[ -f "$SIDECAR" ]] || fail "missing checksum sidecar: $SIDECAR"
[[ -f "$MANIFEST" ]] || fail "missing manifest: $MANIFEST"

if ! ( cd "$BACKUP_DIR" && sha256sum -c "$(basename "$SIDECAR")" ) >>"$LOG_FILE" 2>&1; then
  fail "checksum verification failed for $(basename "$DUMP")"
fi

CHECKSUM="$(sha256sum "$DUMP" | awk '{print $1}')"
SIZE_BYTES="$(stat -c %s "$DUMP")"
MANIFEST_SHA="$(grep -E '^sha256=' "$MANIFEST" | tail -n 1 | cut -d= -f2- || true)"
MANIFEST_SIZE="$(grep -E '^size_bytes=' "$MANIFEST" | tail -n 1 | cut -d= -f2- || true)"
MANIFEST_FILE="$(grep -E '^file=' "$MANIFEST" | tail -n 1 | cut -d= -f2- || true)"
[[ "$MANIFEST_SHA" == "$CHECKSUM" ]] || fail "manifest sha256 does not match the dump"
[[ "$MANIFEST_SIZE" == "$SIZE_BYTES" ]] || fail "manifest size does not match the dump"
[[ "$MANIFEST_FILE" == "$(basename "$DUMP")" ]] || fail "manifest filename does not match the dump"

END_EPOCH="$(date +%s)"
DURATION=$((END_EPOCH - START_EPOCH))
{
  printf 'status=success\n'
  printf 'timestamp_utc=%s\n' "$(date -u -d "@$END_EPOCH" +%Y-%m-%dT%H:%M:%SZ)"
  printf 'timestamp_epoch=%s\n' "$END_EPOCH"
  printf 'database=%s\n' "$DATABASE"
  printf 'label=%s\n' "$LABEL"
  printf 'file=%s\n' "$(basename "$DUMP")"
  printf 'size_bytes=%s\n' "$SIZE_BYTES"
  printf 'sha256=%s\n' "$CHECKSUM"
  printf 'duration_seconds=%s\n' "$DURATION"
  printf 'log=%s\n' "$LOG_FILE"
} > "$STATUS_DIR/.last-success.tmp"
mv -f "$STATUS_DIR/.last-success.tmp" "$STATUS_DIR/last-success"
chmod 600 "$STATUS_DIR/last-success"
rm -f "$STATUS_DIR/last-failure"

log "backup verified: $(basename "$DUMP") (${SIZE_BYTES} bytes, ${DURATION}s)"
notify "success" "pharmacy-backup OK file=$(basename "$DUMP") sha256=$CHECKSUM duration=${DURATION}s" || true
log "done"
