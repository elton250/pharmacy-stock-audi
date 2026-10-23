#!/usr/bin/env bash
# Restore a PostgreSQL dump — into a DISPOSABLE database only.
#
#   scripts/restore-db.sh --drill backups/pharmacy_backup_20261022_120000.dump
#   scripts/restore-db.sh --drill --keep backups/....dump   # keep the drill DB
#
# SAFETY RULES (non-negotiable, enforced in code below):
#   * the target database is newly created and dropped afterwards unless
#     --keep is used; --allow-existing may reuse an existing EMPTY drill DB;
#   * restoring over the live `pharmacy` database is IMPOSSIBLE: the script
#     refuses the name `pharmacy` and refuses a target containing any tables;
#   * the live database is never modified, dropped, truncated or truncated-
#     replaced — the drill works on a throwaway copy only.
#
# The drill verifies the backup end to end: it restores it, checks that the
# restored copy is readable, and optionally compares row counts with the source.

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
COMPARE_LIVE=0
EXPECTED_REV=""
KEY_TABLES=""

log()  { printf '[restore-db] %s\n' "$*"; }
fail() { printf '[restore-db] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: scripts/restore-db.sh --drill <dumpfile> [--keep] [--allow-existing]
  --drill               required: this script only ever restores into a disposable DB
  --keep                keep the drilled database instead of dropping it
  --allow-existing      permit an existing, EMPTY, owned drill database; requires --keep
  --no-live-compare     default: never read the source database; verify the
                        restored copy on its own
  --compare-source      explicit opt-in: read the source row counts and compare
                        them with the restored copy (requires approval)
  --expected-revision R require the restored alembic_version to equal R
  --key-tables LIST     comma-separated tables that must exist in both compared
                        databases (e.g. users,medicines,audit_logs)
EOF
}

DRILL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --drill) DRILL=1; shift ;;
    --keep) KEEP=1; shift ;;
    --allow-existing) ALLOW_EXISTING=1; shift ;;
    --no-live-compare) COMPARE_LIVE=0; shift ;;
    --compare-source) COMPARE_LIVE=1; shift ;;
    --expected-revision) EXPECTED_REV="$2"; shift 2 ;;
    --key-tables) KEY_TABLES="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; fail "unknown argument: $1" ;;
    *) DUMP="$1"; shift ;;
  esac
done
if [[ $COMPARE_LIVE -eq 1 && "${PHARMACY_ALLOW_SOURCE_COMPARE:-0}" != "1" ]]; then
  fail "--compare-source requires PHARMACY_ALLOW_SOURCE_COMPARE=1 (explicit approval)"
fi
if (( ALLOW_EXISTING == 1 && KEEP == 0 )); then
  fail "--allow-existing requires --keep so cleanup never drops a database it did not create"
fi
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

# A dump knows which database it came from (backup-db.sh writes a manifest).
# Refuse to compare it against a different source: restoring a pharmacy_test
# dump and comparing it to live `pharmacy` would produce a meaningless verdict.
DUMP_MANIFEST="$DUMP.manifest"
if [[ -f "$DUMP_MANIFEST" ]]; then
  MANIFEST_DB="$(grep -E '^database=' "$DUMP_MANIFEST" | tail -n 1 | cut -d= -f2- || true)"
  if [[ -n "$MANIFEST_DB" && "$MANIFEST_DB" != "$SOURCE_DB" ]]; then
    fail "dump manifest belongs to '$MANIFEST_DB' but the env file points at '$SOURCE_DB' — refusing to compare mismatched databases"
  fi
  log "dump manifest database matches source: $SOURCE_DB"
else
  log "WARNING: no manifest beside dump; source database cannot be cross-checked"
fi

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

TARGET_EXISTS=0
TARGET_CREATED=0
if exists_db "$TARGET_DB"; then
  TARGET_EXISTS=1
fi
if (( TARGET_EXISTS == 1 )); then
  if (( ALLOW_EXISTING == 0 )); then
    fail "target database '$TARGET_DB' already exists (refusing to overwrite existing data; use --allow-existing only for an empty, disposable database)"
  fi
  if [[ "$TARGET_DB" == "pharmacy" || "$TARGET_DB" == "$SOURCE_DB" ]]; then
    fail "refusing to use --allow-existing with live/source database name '$TARGET_DB'"
  fi
  OWNER_INFO="$("$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d postgres -tAc \
    "SELECT current_user || '|' || pg_get_userbyid(datdba) FROM pg_database WHERE datname='$TARGET_DB'" 2>/dev/null | tr -d '\r\n' || true)"
  if [[ "$OWNER_INFO" != *"|"* ]]; then
    fail "could not determine ownership of existing target database '$TARGET_DB'; refusing --allow-existing"
  fi
  DB_CURRENT_ROLE="${OWNER_INFO%%|*}"
  DB_OWNER_ROLE="${OWNER_INFO#*|}"
  if [[ -z "$DB_CURRENT_ROLE" || "$DB_CURRENT_ROLE" != "$DB_OWNER_ROLE" ]]; then
    fail "existing target '$TARGET_DB' is owned by '$DB_OWNER_ROLE', not the current role '$DB_CURRENT_ROLE'; refusing --allow-existing"
  fi
  EXISTING_OBJECTS="$("$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$TARGET_DB" -tAc "
    SELECT
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
          AND n.nspname NOT LIKE 'pg_%'
          AND c.relkind IN ('r','p','v','m','S','f')),
      (SELECT count(*) FROM pg_namespace
        WHERE nspname NOT IN ('pg_catalog','information_schema','pg_toast','public')
          AND nspname NOT LIKE 'pg_%'),
      (SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql');" 2>/dev/null || true)"
  EXISTING_OBJECTS="${EXISTING_OBJECTS//[[:space:]]/}"
  if [[ ! "$EXISTING_OBJECTS" =~ ^[0-9]+\|[0-9]+\|[0-9]+$ ]]; then
    fail "could not inspect existing target database '$TARGET_DB'; refusing --allow-existing"
  fi
  IFS='|' read -r EXISTING_RELATIONS EXISTING_SCHEMAS EXISTING_EXTENSIONS <<< "$EXISTING_OBJECTS"
  if (( EXISTING_RELATIONS > 0 || EXISTING_SCHEMAS > 0 || EXISTING_EXTENSIONS > 0 )); then
    fail "target database '$TARGET_DB' contains unexpected objects (relations=$EXISTING_RELATIONS, extra_schemas=$EXISTING_SCHEMAS, extensions=$EXISTING_EXTENSIONS); refusing --allow-existing"
  fi
  log "WARNING: --allow-existing: target '$TARGET_DB' is owned by the current role and empty; --keep prevents deleting it after the drill"
fi

if [[ $COMPARE_LIVE -eq 1 ]]; then
  log "source database : $SOURCE_DB (explicit --compare-source; PostgreSQL will be contacted for read-only counts)"
else
  log "source database : $SOURCE_DB not queried (default --no-live-compare; PostgreSQL connection to source not required for this verification)"
fi
if (( KEEP == 1 )); then
  log "target database : $TARGET_DB (kept after verification)"
else
  log "target database : $TARGET_DB (disposable, dropped afterwards if created by this run)"
fi

# ----------------------------------------------------------- verify the dump
LIST_RC=0
"$PG_RESTORE" --list "$DUMP" >/dev/null 2>&1 || LIST_RC=$?
if (( LIST_RC != 0 )); then
  fail "the dump is not a readable archive (pg_restore --list failed, exit status ${LIST_RC}) — refusing to restore"
fi
log "dump archive verified with pg_restore --list"

# ------------------------------------------------------------------- create
if (( TARGET_EXISTS == 0 )); then
  "$CREATEDB" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" "$TARGET_DB" \
    || fail "could not create the disposable database"
  TARGET_CREATED=1
  log "created disposable database"
fi

CLEANUP_FAILED=0
cleanup_db() {
  local rc=$?
  if [[ $KEEP -eq 0 && $TARGET_CREATED -eq 1 ]]; then
    if "$DROPDB" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" --if-exists "$TARGET_DB" >/dev/null 2>&1; then
      log "dropped disposable database"
    else
      CLEANUP_FAILED=1
      log "WARNING: could not drop $TARGET_DB — drop it manually"
    fi
  elif [[ $KEEP -eq 0 ]]; then
    log "not dropping pre-existing target database: $TARGET_DB (it was not created by this run)"
  else
    log "kept disposable database: $TARGET_DB"
  fi
  if (( CLEANUP_FAILED == 1 )); then
    if (( rc == 0 )); then
      printf '[restore-db] ERROR: cleanup failed (could not drop disposable database %s)\n' "$TARGET_DB" >&2
      exit 1
    else
      printf '[restore-db] WARNING: cleanup failed (could not drop disposable database %s); preserving original failure (exit %s)\n' "$TARGET_DB" "$rc" >&2
      exit "$rc"
    fi
  fi
  exit "$rc"
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

check_key_tables() {
  local counts="$1" where="$2" table
  local -a required=()
  [[ -n "$KEY_TABLES" ]] || return 0
  IFS=',' read -r -a required <<< "$KEY_TABLES"
  for table in "${required[@]}"; do
    table="${table//[[:space:]]/}"
    [[ -n "$table" ]] || continue
    if ! printf '%s\n' "$counts" | cut -d= -f1 | grep -Fxq "$table"; then
      fail "key table '$table' missing from $where"
    fi
  done
}

if [[ -n "$EXPECTED_REV" ]]; then
  RESTORED_REV="$("$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$TARGET_DB" -tAc \
    "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null || true)"
  if [[ "$RESTORED_REV" != "$EXPECTED_REV" ]]; then
    fail "alembic revision mismatch: expected '$EXPECTED_REV', restored '${RESTORED_REV:-<missing>}'"
  fi
  log "alembic revision matches expected: $EXPECTED_REV"
fi

if [[ $COMPARE_LIVE -eq 0 ]]; then
  DST_COUNTS="$(row_counts "$TARGET_DB" || true)"
  if [[ -z "$DST_COUNTS" ]]; then
    fail "could not read the restored database — verification failed"
  fi
  RESTORED_REV="$("$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$TARGET_DB" -tAc \
    "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null || true)"
  if [[ -z "$RESTORED_REV" ]]; then
    fail "restored database has no alembic_version row — verification failed"
  fi
  check_key_tables "$DST_COUNTS" "restored database"
  echo
  log "restored table counts (source data not queried; restored database only):"
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    tbl="${line%%=*}"; rows="${line##*=}"
    printf '  OK    %-28s %s rows\n' "$tbl" "$rows"
  done <<<"$DST_COUNTS"
  echo
  log "RESTORE VERIFICATION PASSED: restored copy is readable (revision $RESTORED_REV)"
  exit 0
fi

SRC_COUNTS="$(row_counts "$SOURCE_DB" || true)"
DST_COUNTS="$(row_counts "$TARGET_DB" || true)"

if [[ -z "$SRC_COUNTS" ]]; then
  fail "could not read the source database '$SOURCE_DB' — refusing to claim a match"
fi
if [[ -z "$DST_COUNTS" ]]; then
  fail "could not read the restored database — verification failed"
fi

check_key_tables "$SRC_COUNTS" "source database"
check_key_tables "$DST_COUNTS" "restored database"

echo
log "row-count comparison (source $SOURCE_DB vs restored $TARGET_DB):"
DIFFS=0
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  tbl="${line%%=*}"; src="${line##*=}"
  dst="$(printf '%s\n' "$DST_COUNTS" | awk -F= -v table="$tbl" '$1 == table { print $2; exit }')"
  if [[ "$src" == "$dst" ]]; then
    printf '  OK    %-28s %s rows\n' "$tbl" "$src"
  else
    printf '  DIFF  %-28s source=%s restored=%s\n' "$tbl" "$src" "${dst:-<missing>}"
    DIFFS=$((DIFFS + 1))
  fi
done <<<"$SRC_COUNTS"

while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  tbl="${line%%=*}"; restored="${line##*=}"
  if ! printf '%s\n' "$SRC_COUNTS" | cut -d= -f1 | grep -Fxq "$tbl"; then
    printf '  DIFF  %-28s source=<missing> restored=%s\n' "$tbl" "$restored"
    DIFFS=$((DIFFS + 1))
  fi
done <<<"$DST_COUNTS"

echo
if (( DIFFS == 0 )); then
  log "RESTORE DRILL PASSED: every table in $SOURCE_DB matches the restored copy"
  exit 0
fi
log "RESTORE DRILL FAILED: $DIFFS table(s) differ"
exit 1
