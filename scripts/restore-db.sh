#!/usr/bin/env bash
# Restore a PostgreSQL dump — into a DISPOSABLE database only.
#
#   scripts/restore-db.sh --drill backups/pharmacy_backup_20261022_120000.dump
#   scripts/restore-db.sh --drill --keep backups/....dump   # keep the drill DB
#
# SAFETY RULES (non-negotiable, enforced in code below):
#   * the target database is ALWAYS newly created and dropped afterwards;
#   * restoring over the live `pharmacy` database is IMPOSSIBLE: the script
#     refuses the name `pharmacy` and refuses any database that already exists
#     unless --allow-existing is given, and even then it refuses `pharmacy`;
#   * the live database is never modified, dropped, truncated or truncated-
#     replaced — the drill works on a throwaway copy only.
#
# The drill verifies the backup end to end: it restores it, compares the row
# counts of every table against the source database, and reports a verdict.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PHARMACY_ENV_FILE:-$ROOT_DIR/backend/.env}"
PG_BIN="${PHARMACY_PG_BIN:-/usr/lib/postgresql/18/bin}"
PG_RESTORE="$PG_BIN/pg_restore"
PSQL="$PG_BIN/psql"
CREATEDB="$PG_BIN/createdb"
DROPDB="$PG_BIN/dropdb"

DUMP=""
KEEP=0
ALLOW_EXISTING=0

log()  { printf '[restore-db] %s\n' "$*"; }
fail() { printf '[restore-db] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: scripts/restore-db.sh --drill <dumpfile> [--keep] [--allow-existing]
  --drill           required: this script only ever restores into a disposable DB
  --keep            keep the drilled database instead of dropping it
  --allow-existing  permit restoring into an existing non-live database
EOF
}

DRILL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --drill) DRILL=1; shift ;;
    --keep) KEEP=1; shift ;;
    --allow-existing) ALLOW_EXISTING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; fail "unknown argument: $1" ;;
    *) DUMP="$1"; shift ;;
  esac
done
[[ $DRILL -eq 1 ]] || { usage >&2; fail "--drill is required (this script never restores over a live database)"; }
[[ -n "$DUMP" ]] || { usage >&2; fail "dump file path is required"; }

[[ -f "$DUMP" ]] || fail "dump file not found: $DUMP"
[[ -s "$DUMP" ]] || fail "dump file is empty: $DUMP (this is exactly the 0-byte failure the backup script prevents)"
[[ -x "$PG_RESTORE" ]] || fail "pg_restore not found at $PG_RESTORE"
[[ -x "$PSQL" ]] || fail "psql not found at $PSQL"
[[ -f "$ENV_FILE" ]] || fail "backend/.env not found: $ENV_FILE"

read_env_value() {
  local key="$1" line value
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  value="${value%$'\r'}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

DSN="$(read_env_value DATABASE_URL || true)"
[[ -n "$DSN" ]] || fail "DATABASE_URL not set in $ENV_FILE"
rest="${DSN#*://}"
creds="${rest%%@*}"; hostpart="${rest#*@}"
hostpart="${hostpart%%/*}"; SOURCE_DB="${rest##*/}"
PGUSER_="${creds%%:*}"; PGPASSWORD_="${creds#*:}"
[[ "$PGPASSWORD_" == "$creds" ]] && PGPASSWORD_=""
PGHOST_="${hostpart%%:*}"; PGPORT_="${hostpart##*:}"
[[ "$PGPORT_" == "$hostpart" ]] && PGPORT_="5432"
[[ -n "$hostpart" ]] || PGHOST_="127.0.0.1"
export PGPASSWORD="$PGPASSWORD_"

TARGET_DB="pharmacy_restore_drill_$(date -u +%Y%m%d_%H%M%S)"

# ---------------------------------------------------------- safety interlocks
if [[ "$TARGET_DB" == "pharmacy" || "$TARGET_DB" == "${SOURCE_DB}" ]]; then
  fail "refusing to use a live database name as the restore target"
fi
if [[ "$SOURCE_DB" == "pharmacy" && "$TARGET_DB" == "pharmacy" ]]; then
  fail "refusing to restore over the live database"
fi

exists_db() {
  "$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$1'" 2>/dev/null | grep -q 1
}

if exists_db "$TARGET_DB" && [[ $ALLOW_EXISTING -eq 0 ]]; then
  fail "target database '$TARGET_DB' already exists (refusing to overwrite; use --allow-existing for non-live databases)"
fi

log "source database : $SOURCE_DB (read-only comparison only)"
log "target database : $TARGET_DB (disposable, dropped afterwards)"

# ----------------------------------------------------------- verify the dump
LIST_RC=0
"$PG_RESTORE" --list "$DUMP" >/dev/null 2>&1 || LIST_RC=$?
if (( LIST_RC != 0 )); then
  fail "the dump is not a readable archive (pg_restore --list failed, exit status ${LIST_RC}) — refusing to restore"
fi
log "dump archive verified with pg_restore --list"

# ------------------------------------------------------------------- create
if ! exists_db "$TARGET_DB"; then
  "$CREATEDB" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" "$TARGET_DB" \
    || fail "could not create the disposable database"
  log "created disposable database"
fi

cleanup_db() {
  if [[ $KEEP -eq 0 ]]; then
    "$DROPDB" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" --if-exists "$TARGET_DB" >/dev/null 2>&1 \
      && log "dropped disposable database" \
      || log "WARNING: could not drop $TARGET_DB — drop it manually"
  else
    log "kept disposable database: $TARGET_DB"
  fi
}
trap cleanup_db EXIT

# ------------------------------------------------------------------ restore
log "restoring into $TARGET_DB ..."
RESTORE_RC=0
"$PG_RESTORE" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" --no-owner --no-privileges --exit-on-error -d "$TARGET_DB" "$DUMP" || RESTORE_RC=$?
if (( RESTORE_RC != 0 )); then
  fail "pg_restore failed (exit status ${RESTORE_RC}) — the disposable database was discarded"
fi
log "restore completed"

# ------------------------------------------------------------- row-count diff
row_counts() {
  local db="$1"
  "$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$db" -tAc "
    SELECT table_name || '=' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text
    FROM information_schema.tables
    WHERE table_schema='public' AND table_type='BASE TABLE'
    ORDER BY table_name;" 2>/dev/null
}

SRC_COUNTS="$(row_counts "$SOURCE_DB" || true)"
DST_COUNTS="$(row_counts "$TARGET_DB" || true)"

if [[ -z "$DST_COUNTS" ]]; then
  fail "could not read the restored database — verification failed"
fi

echo
log "row-count comparison (source $SOURCE_DB vs restored $TARGET_DB):"
DIFFS=0
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  tbl="${line%%=*}"; src="${line##*=}"
  dst="$(printf '%s\n' "$DST_COUNTS" | grep -E "^${tbl}=" | head -n 1 | cut -d= -f2)"
  if [[ "$src" == "$dst" ]]; then
    printf '  OK    %-28s %s rows\n' "$tbl" "$src"
  else
    printf '  DIFF  %-28s source=%s restored=%s\n' "$tbl" "$src" "${dst:-<missing>}"
    DIFFS=$((DIFFS + 1))
  fi
done <<<"$SRC_COUNTS"

echo
if (( DIFFS == 0 )); then
  log "RESTORE DRILL PASSED: every table in $SOURCE_DB matches the restored copy"
  exit 0
fi
log "RESTORE DRILL FAILED: $DIFFS table(s) differ"
exit 1
