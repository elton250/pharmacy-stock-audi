# Pharmacy Stock Auditing System

Barcode-first stock auditing for pharmacies: medicines, package conversions, inventory transactions, strict static counting sessions, physical stock audits and variance reports — with RBAC and an append-only audit log.

- **Backend:** FastAPI + PostgreSQL 18 + Alembic (`backend/`)
- **Frontend:** React + TypeScript + Vite (`frontend/`)
- **Status:** Phase 1 complete. See `PHASE1_REPORT.md` for the full report.

## Requirements

- Python 3.14+, Node 24+, npm 11+, PostgreSQL 14+ (dev uses a user-owned instance on port 54329 — see `.env`)

## Start everything (recommended)

After a reboot nothing is running yet, so start the stack with one command. It starts
PostgreSQL → backend → frontend **in that order** and waits for each layer to become
healthy before continuing:

```bash
scripts/start-dev.sh            # start (idempotent — safe to run again)
scripts/start-dev.sh --check    # status of every layer
scripts/start-dev.sh --stop     # stop backend + frontend (PostgreSQL keeps running)
```

All runtime files stay inside the project:

| Path | Purpose |
|---|---|
| `runtime/postgres.log` | PostgreSQL server log |
| `runtime/pgsock/` | PostgreSQL unix socket directory |
| `runtime/logs/backend.log` | API log |
| `runtime/logs/frontend.log` | Vite log |
| `runtime/*.pid` | PIDs of the processes started by the script |

### Diagnosing the "502 Bad Gateway" page

The Vite dev server proxies `/api` to the backend. If the backend (or the database
it needs) is not running, the UI loads but every API call returns **502**. Check which
layer is down:

```bash
scripts/check-health.sh
```

It prints one line per layer and tails the relevant log. `curl http://127.0.0.1:8000/api/health`
answers `{"status":"ok","database":"up"}` when everything is healthy and HTTP 503 with
a reason when the database is unreachable.

## Database

This project runs against a **user-owned PostgreSQL instance** (no sudo available in the dev environment).
The data directory is `~/.local/share/pharmacy-postgres`; the socket and log live inside the project:

```bash
# Start the instance (this is what scripts/start-dev.sh does)
pg_ctl -D ~/.local/share/pharmacy-postgres \
  -o "-p 54329 -k $PWD/runtime/pgsock" -l $PWD/runtime/postgres.log start
pg_isready -h 127.0.0.1 -p 54329 -U pharmacy   # should report accepting connections
```

Databases: `pharmacy` (dev), `pharmacy_test` (tests). User: `pharmacy`; password: the value set in `backend/.env` (`DATABASE_URL`).

## Run the backend

```bash
cd backend
python -m venv .venv                 # first time only
.venv/bin/pip install -r requirements.txt   # first time only
cp env.example .env                  # first time only; fill in the CHANGE_ME placeholders
.venv/bin/alembic upgrade head       # first time only — creates all tables
.venv/bin/python run.py --port 8000
```

API documentation (Swagger UI): `http://127.0.0.1:8000/api/docs` · ReDoc: `/api/redoc` · OpenAPI JSON: `/api/openapi.json`.

First-run bootstrap — **create the initial administrator via the CLI** (only possible while no administrator exists; an existing admin is never overwritten):

```bash
# Local development (suggested credentials)
.venv/bin/python -m app.cli create-admin \
  --username admin \
  --email admin@pharmacy.local \
  --password 'Admin@Pharma2026!'

# Production: never pass --password on the command line — use the hidden prompt
.venv/bin/python -m app.cli create-admin --username admin --email ops@yourpharmacy.example

# …or provide the password through the environment
PHARMACY_ADMIN_PASSWORD='…' .venv/bin/python -m app.cli create-admin --username admin --email ops@yourpharmacy.example
```

Passwords are stored as argon2 hashes only (plaintext is never persisted or echoed), and the created admin has `must_change_password=true` — after the first login the API blocks every endpoint (403 `password_change_required`) until a new password is set via `POST /api/auth/change-password` or the `/change-password` page. Changing the password revokes all other sessions.

Alternative web bootstrap (only when zero users exist): open the frontend → `/setup`, which also creates the first administrator.

### Password recovery (lost admin password)

If an administrator's password is unknown, reset it **in place** — the account is preserved (never deleted or duplicated), all its sessions are revoked, and a password change is required at next login:

```bash
# Interactive (hidden prompt, type YES to confirm)
.venv/bin/python -m app.cli reset-admin-password --username admin

# Non-interactive
.venv/bin/python -m app.cli reset-admin-password --username admin --yes \
  --password '…'            # or PHARMACY_ADMIN_PASSWORD env / hidden prompt
```

### Login contract

`POST /api/auth/login` authenticates by **`username`** only (plus `password`). Email is stored for contact purposes and is not a login identifier — sending `email` instead of `username` is a 422 validation error.

## Run the frontend

```bash
cd frontend
npm install          # first time only
npm run dev          # http://localhost:5173 (proxies /api to 127.0.0.1:8000)
```

Production build: `npm run build` (output in `frontend/dist/`).

## Tests

```bash
cd backend
.venv/bin/python -m pytest -q        # 147 passed
```

Tests use the `pharmacy_test` database (see `backend/tests/conftest.py`); tables are truncated at fixture setup only.

## Roles & permissions (seeded defaults)

| Role | Permissions |
|---|---|
| `administrator` | all |
| `pharmacist` | `medicine:view`, `inventory:view`, `inventory:transact`, `count:perform`, `count:review`, `count:manage`, `audit:view`, `audit:initiate`, `audit:manage`, `report:view` |
| `stock_counter` | `medicine:view`, `count:perform` |

Auth: argon2 password hashing, server-side session cookies (`pharmacy_session` HttpOnly + `pharmacy_csrf`), `X-CSRF-Token` required on mutating requests, login rate-limited (5 failures / 15 min per process).

## Core rules

- **Static counting:** one medicine per session; medicine mismatch is rejected with `409 medicine_mismatch`; only the session creator records counts; review requires a different user who is neither the creator nor any operator of the session.
- **One active session only:** at most one counting session may be `DRAFT`, `IN_PROGRESS` or `PENDING_REVIEW` across the whole system. The rule is enforced by the API (`409 active_session_exists`) **and** by a PostgreSQL partial unique index (`uq_counting_sessions_single_active`), so double clicks, multiple tabs and concurrent requests cannot create a second active session. A new session can only be started after the current one is completed (and reviewed) or cancelled.
- **Recovering a stuck session:** the counting page shows an **Active counting session** card with the session ID, code, medicine, barcode, start time, operator and the progress so far, plus **Resume session** and **Cancel session** buttons.
  - **Resume** (`POST /api/counting/sessions/{id}/resume`) reopens *the same* session: no new session, no duplicated count events, no inventory transactions, no reset quantities — the saved progress is returned as-is. Allowed for the creator or a holder of `count:manage` / `count:review`; the status is not changed (a `PENDING_REVIEW` session stays pending review).
  - **Cancel** (`POST /api/counting/sessions/{id}/cancel`, optional `reason`) is allowed for the **creator or any holder of `count:manage`** — a supervisor can therefore release a session whose operator is unavailable. Unrelated operators get `403 forbidden`. The cancellation (status, timestamp, actor, audit entry) is written in one transaction, so the single-active-session rule is released only once it really succeeded; the actor is stored in `counting_sessions.cancelled_by` and shown in the UI. The optional reason (≤ 500 characters) is offered in the confirmation dialog and stored in the audit log next to the actor.
  - Both endpoints answer with precise codes: `404 not_found`, `409 archived` / `already_completed` / `already_cancelled` / `invalid_state` / `active_session_exists`, `403 forbidden`.
- **Cancelled sessions never count:** a cancelled session contributes nothing to physical stock, variance or stock-level reports. It is still stored (with all its count events) and can be archived.
- **Archiving (soft delete):** a pharmacist/administrator can archive a cancelled or draft session to hide it from the normal list (`POST /api/counting/sessions/{id}/archive`, requires `count:manage`). The record, its events and the audit-log entry are preserved and the session can be restored with `POST /api/counting/sessions/{id}/unarchive`. Nothing is ever hard-deleted.
- **Re-counting supersedes:** if a medicine is counted again, the newest completed count replaces the previous one instead of adding to it.
- **Uncounted ≠ shortage:** a medicine without a completed, non-archived count is reported as `UNCOUNTED` with no physical stock and no variance — never as a false shortage.
- **Idempotency:** every count event carries a client-generated `idempotency_key` (globally unique, min 8 chars); replays return the original event.
- **Expected stock** = OPENING + RECEIVED + RETURN_IN + ADJ_POS − ISSUED − RETURN_OUT − ADJ_NEG, computed from transactions only. Opening stock becomes immutable once any other transaction exists.
- **Variances are reported, never auto-corrected.** Inventory adjustments are separate, explicitly authorized transactions.
- **Package conversion:** top-level packages (`parent_id = NULL`) multiply against base units via `units_per_parent`; a cut/partial package line uses `partial_units`.
- **Audit log:** append-only, with before/after JSON, actor, IP and timestamp for logins, user/medicine/barcode/package changes, transactions, sessions, counts, reviews, cancel/resume/archive/restore actions and audit lifecycle events.
