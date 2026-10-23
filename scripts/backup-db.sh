#!/usr/bin/env bash
# Validated PostgreSQL backup for the pharmacy stack.
#
#   scripts/backup-db.sh                     # back up the configured database
#   scripts/backup-db.sh --database pharmacy_test
#   scripts/backup-db.sh --retention-days 30 --keep 10
#
# WHY THIS EXISTS
# A previous manual dump (backups/pharmacy_pre_fix_20261021_095349.dump) was
# left behind as a 0-byte file: the shell redirection created the target file
# even though pg_dump failed before it ever connected, and neither the exit
# status nor the resulting size was checked. A failed backup therefore looked
# exactly like a successful one.
#
# This script makes that failure mode impossible:
#   1. the dump is written to a temporary file, never straight to the target;
#   2. pg_dump's exit status is checked;
#   3. the file must be non-empty;
#   4. the archive is verified with `pg_restore --list` (custom format TOC);
#   5. only then is the file published with an atomic no-clobber hard link;
#   6. on any failure the temporary file is removed and the exit status is
#      non-zero, so a caller cannot mistake it for a backup.
#
# The password is read from backend/.env and passed via PGPASSWORD; it is
# never echoed, never logged and never stored in the dump metadata.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${PHARMACY_BACKUP_DIR:-$ROOT_DIR/backups}"
ENV_FILE="${PHARMACY_ENV_FILE:-$ROOT_DIR/backend/.env}"
PG_BIN="${PHARMACY_PG_BIN:-/usr/lib/postgresql/18/bin}"
PG_DUMP="$PG_BIN/pg_dump"
PG_RESTORE="$PG_BIN/pg_restore"

DATABASE=""
LABEL="backup"
RETENTION_DAYS=30
KEEP=10

log()  { printf '[backup-db] %s\n' "$*"; }
fail() { printf '[backup-db] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: scripts/backup-db.sh [options]
  --database NAME     database to dump (default: from backend/.env DATABASE_URL)
  --label NAME        label embedded in the file name (default: backup)
  --retention-days N  delete dumps older than N days (default: 30, 0 disables)
  --keep N            always keep at least N newest dumps (default: 10)
  -h, --help          show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --database) DATABASE="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

[[ -x "$PG_DUMP" ]] || fail "pg_dump not found at $PG_DUMP (set PHARMACY_PG_BIN)"
[[ -x "$PG_RESTORE" ]] || fail "pg_restore not found at $PG_RESTORE (set PHARMACY_PG_BIN)"
[[ -f "$ENV_FILE" ]] || fail "backend/.env not found: $ENV_FILE"

# ---------------------------------------------------------------- connection
# Parse DATABASE_URL without ever printing it.
PGHOST_=""; PGPORT_=""; PGUSER_=""; PGPASSWORD_=""; DBNAME_=""
read_env_value() {
  local key="$1" line value
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  value="${value%$'\r'}"
  # strip surrounding quotes if present
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

DSN="$(read_env_value DATABASE_URL || true)"
[[ -n "$DSN" ]] || fail "DATABASE_URL not set in $ENV_FILE"

# postgresql+psycopg://user:pass@host:port/db -> libpq parts
strip_scheme() { printf '%s' "${1#*://}"; }
rest="$(strip_scheme "$DSN")"
creds="${rest%%@*}"; hostpart="${rest#*@}"
hostpart="${hostpart%%/*}"; DBNAME_="${rest##*/}"
if [[ "$rest" != *"/"* ]]; then DBNAME_=""; fi
PGUSER_="${creds%%:*}"; PGPASSWORD_="${creds#*:}"
[[ "$PGPASSWORD_" == "$creds" ]] && PGPASSWORD_=""
PGHOST_="${hostpart%%:*}"; PGPORT_="${hostpart##*:}"
[[ "$PGPORT_" == "$hostpart" ]] && PGPORT_="5432"
[[ -n "$hostpart" ]] || PGHOST_="127.0.0.1"

if [[ -n "$DATABASE" ]]; then DBNAME_="$DATABASE"; fi
[[ -n "$DBNAME_" ]] || fail "could not determine the database name"

export PGPASSWORD="$PGPASSWORD_"
PGCONNECT=( -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$DBNAME_" )

# ------------------------------------------------------------------- layout
umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

STAMP="${PHARMACY_BACKUP_STAMP:-$(date -u +%Y%m%d_%H%M%S)}"
[[ "$STAMP" =~ ^[0-9]{8}_[0-9]{6}$ ]] || fail "invalid PHARMACY_BACKUP_STAMP: expected YYYYmmdd_HHMMSS"
BASE="${DBNAME_}_${LABEL}_${STAMP}"
TARGET=""
SIDECAR=""
MANIFEST=""
TMPFILE=""
SIDECAR_TMP=""
MANIFEST_TMP=""
TARGET_PUBLISHED=0
SIDECAR_PUBLISHED=0
MANIFEST_PUBLISHED=0
KEEP_ARTIFACTS=0

cleanup() {
  rm -f "$TMPFILE" "$SIDECAR_TMP" "$MANIFEST_TMP"
  if (( KEEP_ARTIFACTS == 0 )); then
    if (( TARGET_PUBLISHED == 1 )); then rm -f "$TARGET"; fi
    if (( SIDECAR_PUBLISHED == 1 )); then rm -f "$SIDECAR"; fi
    if (( MANIFEST_PUBLISHED == 1 )); then rm -f "$MANIFEST"; fi
  fi
}
trap cleanup EXIT
TMPFILE="$(mktemp "$BACKUP_DIR/.incomplete_${BASE}_XXXXXX")" || fail "could not create a temporary dump file"
SIDECAR_TMP="$(mktemp "$BACKUP_DIR/.incomplete_${BASE}_XXXXXX")" || fail "could not create a temporary checksum file"
MANIFEST_TMP="$(mktemp "$BACKUP_DIR/.incomplete_${BASE}_XXXXXX")" || fail "could not create a temporary manifest file"

log "dumping database '$DBNAME_' from ${PGHOST_}:${PGPORT_}"

# ------------------------------------------------------------------- dump
# --format=custom gives a compressed archive that pg_restore can validate.
DUMP_RC=0
"$PG_DUMP" "${PGCONNECT[@]}" --format=custom --no-owner --no-privileges --file="$TMPFILE" || DUMP_RC=$?
if (( DUMP_RC != 0 )); then
  fail "pg_dump failed (exit status ${DUMP_RC}) — no backup was created"
fi

# ------------------------------------------------------- validation (1: size)
if [[ ! -s "$TMPFILE" ]]; then
  fail "pg_dump produced an EMPTY file — refusing to keep it as a backup (this is the 0-byte failure mode)"
fi
SIZE_BYTES="$(stat -c %s "$TMPFILE")"
if (( SIZE_BYTES < 1024 )); then
  fail "pg_dump produced a suspiciously small file (${SIZE_BYTES} bytes) — refusing to keep it"
fi

# ------------------------------------------- validation (2: archive readable)
LIST_RC=0
"$PG_RESTORE" --list "$TMPFILE" >/dev/null 2>"$BACKUP_DIR/.restore_list_err" || LIST_RC=$?
if (( LIST_RC != 0 )); then
  ERR="$(cat "$BACKUP_DIR/.restore_list_err")"
  rm -f "$BACKUP_DIR/.restore_list_err"
  fail "pg_restore --list could not read the archive (exit status ${LIST_RC}) — the dump is not restorable: ${ERR}"
fi
rm -f "$BACKUP_DIR/.restore_list_err"

TOC_ENTRIES="$("$PG_RESTORE" --list "$TMPFILE" | grep -c '^;' || true)"
if [[ -z "$TOC_ENTRIES" || "$TOC_ENTRIES" -lt 1 ]]; then
  fail "archive TOC is empty — refusing to keep it"
fi

# ------------------------------------------------- validation (3: checksum)
CHECKSUM="$(sha256sum "$TMPFILE" | awk '{print $1}')"

# ------------------------------------------------------ publish atomically
publish_target() {
  local index=0 candidate
  while (( index <= 1000 )); do
    if (( index == 0 )); then
      candidate="$BACKUP_DIR/${BASE}.dump"
    else
      candidate="$BACKUP_DIR/${BASE}_${index}.dump"
    fi
    if [[ ! -e "$candidate" && ! -e "$candidate.sha256" && ! -e "$candidate.manifest" ]]; then
      if ln "$TMPFILE" "$candidate" 2>/dev/null; then
        TARGET="$candidate"
        TARGET_PUBLISHED=1
        return 0
      fi
      if [[ ! -e "$candidate" ]]; then
        fail "could not publish dump to $candidate"
      fi
    fi
    index=$((index + 1))
  done
  fail "could not find an unused backup filename for $BASE"
}
publish_target
rm -f "$TMPFILE"
TMPFILE=""
chmod 600 "$TARGET" || fail "could not set dump permissions"

SIDECAR="$TARGET.sha256"
MANIFEST="$TARGET.manifest"
printf '%s  %s\n' "$CHECKSUM" "$(basename "$TARGET")" > "$SIDECAR_TMP" ||
  fail "could not write the checksum sidecar — backup discarded"
if ! ln "$SIDECAR_TMP" "$SIDECAR" 2>/dev/null; then
  fail "could not publish the checksum sidecar — backup discarded"
fi
SIDECAR_PUBLISHED=1
rm -f "$SIDECAR_TMP"
SIDECAR_TMP=""
chmod 600 "$SIDECAR" || fail "could not set checksum permissions"

{
  printf 'database=%s\n' "$DBNAME_"
  printf 'label=%s\n' "$LABEL"
  printf 'file=%s\n' "$(basename "$TARGET")"
  printf 'size_bytes=%s\n' "$SIZE_BYTES"
  printf 'sha256=%s\n' "$CHECKSUM"
  printf 'toc_entries=%s\n' "$TOC_ENTRIES"
  printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'pg_dump_version=%s\n' "$("$PG_DUMP" --version 2>/dev/null | head -n 1 || true)"
} > "$MANIFEST_TMP" || fail "could not write the backup manifest — backup discarded"
if ! ln "$MANIFEST_TMP" "$MANIFEST" 2>/dev/null; then
  fail "could not publish the backup manifest — backup discarded"
fi
MANIFEST_PUBLISHED=1
rm -f "$MANIFEST_TMP"
MANIFEST_TMP=""
chmod 600 "$MANIFEST" || fail "could not set manifest permissions"
KEEP_ARTIFACTS=1

log "backup created: $TARGET"
log "size: ${SIZE_BYTES} bytes, sha256: ${CHECKSUM}"
log "checksum: $SIDECAR"
log "manifest: $MANIFEST"

# ---------------------------------------------------------------- retention
# Retention is deliberately scoped to this database AND this label. Evidence
# dumps such as pharmacy_pre_fix_*.dump, other labels, and test dumps must
# never be pruned by a nightly run.
RETENTION_GLOB="${DBNAME_}_${LABEL}_*.dump"
if [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS > 0 )); then
  mapfile -t OLD < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "$RETENTION_GLOB" -mtime "+${RETENTION_DAYS}" | sort)
  if (( ${#OLD[@]} > 0 )); then
    for f in "${OLD[@]}"; do
      # never prune away the newest K dumps
      NEWER="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "$RETENTION_GLOB" -newer "$f" | wc -l)"
      if (( NEWER >= KEEP )); then
        rm -f "$f" "$f.sha256" "$f.manifest"
        log "pruned expired dump: $(basename "$f")"
      else
        log "kept (within keep window): $(basename "$f")"
      fi
    done
  fi
fi

log "done"
