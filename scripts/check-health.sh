#!/usr/bin/env bash
# Diagnose the "502 Bad Gateway" symptom.
#
# The frontend (Vite) proxies /api to the backend. If the backend is not running
# (or the database it needs is not running) the UI shows 502 for every API call.
# This script shows exactly which layer is down and prints the relevant log tails.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/runtime"
LOG_DIR="$RUNTIME_DIR/logs"
BACKEND_PORT="${PHARMACY_BACKEND_PORT:-8000}"
FRONTEND_PORT="${PHARMACY_FRONTEND_PORT:-5173}"
PG_PORT="${PHARMACY_PG_PORT:-54329}"
PG_ISREADY="${PHARMACY_PG_BIN:-/usr/lib/postgresql/18/bin}/pg_isready"

ok=0
bad=0
check() {
  local label="$1" result="$2"
  if [[ "$result" == "0" ]]; then printf '  [ OK ] %s\n' "$label"; ok=$(( ok + 1 ))
  else printf '  [DOWN] %s\n' "$label"; bad=$(( bad + 1 )); fi
}

printf 'Layer checks\n'
if "$PG_ISREADY" -h 127.0.0.1 -p "$PG_PORT" -U pharmacy >/dev/null 2>&1; then
  check "PostgreSQL (127.0.0.1:$PG_PORT)" 0
else
  check "PostgreSQL (127.0.0.1:$PG_PORT)" 1
fi

backend_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${BACKEND_PORT}/api/health" 2>/dev/null || echo 000)"
if [[ "$backend_code" == "200" ]]; then
  check "Backend /api/health (HTTP $backend_code)" 0
else
  check "Backend /api/health (HTTP $backend_code)" 1
fi

proxy_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:${FRONTEND_PORT}/api/health" 2>/dev/null || echo 000)"
if [[ "$proxy_code" == "200" ]]; then
  check "Frontend proxy /api/health (HTTP $proxy_code)" 0
else
  check "Frontend proxy /api/health (HTTP $proxy_code)" 1
fi

frontend_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:${FRONTEND_PORT}/" 2>/dev/null || echo 000)"
if [[ "$frontend_code" == "200" ]]; then
  check "Frontend page (HTTP $frontend_code)" 0
else
  check "Frontend page (HTTP $frontend_code)" 1
fi

# Nightly backup freshness (status written by scripts/nightly-backup.sh).
BACKUP_NIGHTLY_DIR="${PHARMACY_NIGHTLY_DIR:-$ROOT_DIR/backups/nightly}"
BACKUP_STATUS_DIR="${PHARMACY_BACKUP_STATUS_DIR:-$BACKUP_NIGHTLY_DIR/status}"
BACKUP_SUCCESS="$BACKUP_STATUS_DIR/last-success"
BACKUP_FAILURE="$BACKUP_STATUS_DIR/last-failure"
BACKUP_ALERT_FAILURE="$BACKUP_STATUS_DIR/last-alert-failure"
file_epoch() { stat -c %Y "$1" 2>/dev/null || printf '0'; }
now_epoch="$(date +%s)"
success_epoch=0
failure_epoch=0
[[ -f "$BACKUP_SUCCESS" ]] && success_epoch="$(file_epoch "$BACKUP_SUCCESS")"
[[ -f "$BACKUP_FAILURE" ]] && failure_epoch="$(file_epoch "$BACKUP_FAILURE")"
if (( success_epoch > 0 )); then
  age=$(( now_epoch - success_epoch ))
  if (( failure_epoch > success_epoch )); then
    check "Nightly backup (failure newer than last success)" 1
  elif (( age <= 93600 )); then
    check "Nightly backup (last success $(( age / 3600 ))h ago)" 0
  else
    check "Nightly backup (last success $(( age / 3600 ))h ago)" 1
  fi
elif (( failure_epoch > 0 )); then
  check "Nightly backup (failure; no success record)" 1
elif [[ -d "$BACKUP_STATUS_DIR" || -d "$BACKUP_NIGHTLY_DIR" ]]; then
  check "Nightly backup (no success record)" 1
else
  printf '  [SKIP] Nightly backup (not configured)\n'
fi
if [[ -f "$BACKUP_ALERT_FAILURE" ]]; then
  alert_epoch="$(file_epoch "$BACKUP_ALERT_FAILURE")"
  alert_age=$(( now_epoch - alert_epoch ))
  check "Nightly alert delivery (last failure $(( alert_age / 3600 ))h ago)" 1
fi

if (( bad > 0 )); then
  printf '\nRecent log tails\n'
  for f in "$RUNTIME_DIR/postgres.log" "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log"; do
    [[ -f "$f" ]] || continue
    printf '\n--- %s ---\n' "$f"
    tail -n 15 "$f"
  done
  printf '\nFix with:  scripts/start-dev.sh\n'
fi

printf '\nsummary: %d ok, %d down\n' "$ok" "$bad"
exit $(( bad > 0 ? 1 : 0 ))
