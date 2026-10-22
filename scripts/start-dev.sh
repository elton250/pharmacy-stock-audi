#!/usr/bin/env bash
# Start the whole Phase 1 stack in the correct order, with project-local runtime files.
#
#   scripts/start-dev.sh              # start everything (idempotent)
#   scripts/start-dev.sh --check      # only report status
#   scripts/start-dev.sh --stop       # stop backend + frontend (leaves PostgreSQL running)
#
# Order matters: PostgreSQL -> backend (must pass /api/health) -> frontend.
# The frontend proxies /api to the backend, so a 502 on the UI is exactly the
# symptom of the backend being down (see scripts/check-health.sh).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PG_SOCKET_DIR="$RUNTIME_DIR/pgsock"
PG_DATA_DIR="${PHARMACY_PG_DATA_DIR:-$HOME/.local/share/pharmacy-postgres}"
PG_PORT="${PHARMACY_PG_PORT:-54329}"
PG_LOG="$RUNTIME_DIR/postgres.log"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="${PHARMACY_BACKEND_PORT:-8000}"
FRONTEND_PORT="${PHARMACY_FRONTEND_PORT:-5173}"
VITE_HOST="127.0.0.1"

PG_BIN="${PHARMACY_PG_BIN:-/usr/lib/postgresql/18/bin}"
PG_CTL="$PG_BIN/pg_ctl"
PG_ISREADY="$PG_BIN/pg_isready"

VENV_PY="$ROOT_DIR/backend/.venv/bin/python"
NPM_BIN="${NPM_BIN:-npm}"

log()  { printf '[start-dev] %s\n' "$*"; }
fail() { printf '[start-dev] ERROR: %s\n' "$*" >&2; exit 1; }

ensure_dirs() {
  mkdir -p "$LOG_DIR" "$PG_SOCKET_DIR"
  chmod 700 "$PG_SOCKET_DIR" 2>/dev/null || true
}

pid_alive() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

port_open() {
  local port="$1"
  (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && exec 3<&- && exec 3>&- && return 0
  return 1
}
wait_for() {
  local label="$1" timeout="$2"; shift 2
  local waited=0
  while (( waited < timeout )); do
    if "$@"; then
      log "$label is ready"
      return 0
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
  return 1
}

pg_ready() { "$PG_ISREADY" -h 127.0.0.1 -p "$PG_PORT" -U pharmacy >/dev/null 2>&1; }
backend_ready() {
  curl -fsS --max-time 3 "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" 2>/dev/null \
    | grep -q '"status":"ok"'
}
frontend_ready() {
  curl -fsS --max-time 3 "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1 \
    || curl -fsS --max-time 3 "http://localhost:${FRONTEND_PORT}/" >/dev/null 2>&1
}

start_postgres() {
  if pg_ready; then
    log "PostgreSQL already accepting connections on port $PG_PORT"
    return 0
  fi
  [[ -x "$PG_CTL" ]] || fail "pg_ctl not found at $PG_CTL (set PHARMACY_PG_BIN)"
  [[ -d "$PG_DATA_DIR" ]] || fail "PostgreSQL data directory not found: $PG_DATA_DIR"
  log "starting PostgreSQL (data=$PG_DATA_DIR port=$PG_PORT socket=$PG_SOCKET_DIR)"
  "$PG_CTL" -D "$PG_DATA_DIR" \
    -o "-p $PG_PORT -k $PG_SOCKET_DIR" \
    -l "$PG_LOG" start >>"$LOG_DIR/postgres_ctl.log" 2>&1 \
    || fail "PostgreSQL failed to start — see $PG_LOG"
  wait_for "PostgreSQL" 30 pg_ready || fail "PostgreSQL did not become ready — see $PG_LOG"
}

start_backend() {
  if backend_ready; then
    log "backend already healthy on port $BACKEND_PORT"
    return 0
  fi
  [[ -x "$VENV_PY" ]] || fail "backend virtualenv missing: $VENV_PY (run: cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt)"
  if ! pg_ready; then
    fail "PostgreSQL is not running — refusing to start the backend (it would answer 502/503)"
  fi
  log "applying migrations"
  (cd "$ROOT_DIR/backend" && "$VENV_PY" -m alembic upgrade head) >>"$BACKEND_LOG" 2>&1 \
    || fail "database migrations failed — see $BACKEND_LOG"
  log "starting backend on ${BACKEND_HOST}:${BACKEND_PORT}"
  (
    cd "$ROOT_DIR/backend"
    nohup "$VENV_PY" run.py --host "$BACKEND_HOST" --port "$BACKEND_PORT" \
      >>"$BACKEND_LOG" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )
  if ! wait_for "backend /api/health" 45 backend_ready; then
    tail -n 30 "$BACKEND_LOG" >&2 || true
    fail "backend did not become healthy — see $BACKEND_LOG"
  fi
}

start_frontend() {
  if frontend_ready; then
    log "frontend already running on port $FRONTEND_PORT"
    return 0
  fi
  [[ -d "$ROOT_DIR/frontend/node_modules" ]] || fail "frontend dependencies missing — run: cd frontend && npm install"
  log "starting frontend on ${VITE_HOST}:${FRONTEND_PORT}"
  (
    cd "$ROOT_DIR/frontend"
    nohup "$NPM_BIN" run dev -- --host "$VITE_HOST" --port "$FRONTEND_PORT" \
      >>"$FRONTEND_LOG" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )
  if ! wait_for "frontend" 45 frontend_ready; then
    tail -n 30 "$FRONTEND_LOG" >&2 || true
    fail "frontend did not become ready — see $FRONTEND_LOG"
  fi
}

status_report() {
  printf '[start-dev] project   : %s\n' "$ROOT_DIR"
  if pg_ready; then printf '[start-dev] postgres  : up   (127.0.0.1:%s)\n' "$PG_PORT"
  else printf '[start-dev] postgres  : DOWN (127.0.0.1:%s)\n' "$PG_PORT"; fi
  if backend_ready; then printf '[start-dev] backend   : up   (http://%s:%s/api/health)\n' "$BACKEND_HOST" "$BACKEND_PORT"
  else printf '[start-dev] backend   : DOWN (http://%s:%s/api/health)\n' "$BACKEND_HOST" "$BACKEND_PORT"; fi
  if frontend_ready; then printf '[start-dev] frontend  : up   (http://localhost:%s)\n' "$FRONTEND_PORT"
  else printf '[start-dev] frontend  : DOWN (http://localhost:%s)\n' "$FRONTEND_PORT"; fi
}

stop_stack() {
  for pid_file in "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"; do
    if pid_alive "$pid_file"; then
      local pid
      pid="$(cat "$pid_file")"
      log "stopping pid $pid ($(basename "$pid_file"))"
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  done
  # Also stop any backend/frontend process of THIS project that was started
  # manually (no pid file). The patterns are project-scoped.
  pkill -f "$ROOT_DIR/backend/run.py" 2>/dev/null && log "stopped manually started backend" || true
  pkill -f "$ROOT_DIR/frontend/node_modules/.bin/vite" 2>/dev/null && log "stopped manually started frontend" || true
  sleep 1
  log "backend and frontend stopped (PostgreSQL left running)"
}

main() {
  ensure_dirs
  case "${1:-start}" in
    --check|status)
      status_report
      ;;
    --stop)
      stop_stack
      ;;
    start|"")
      start_postgres
      start_backend
      start_frontend
      status_report
      log "ready — open http://${VITE_HOST}:${FRONTEND_PORT}/"
      ;;
    *)
      fail "unknown argument: $1 (use start | --check | --stop)"
      ;;
  esac
}

main "$@"
