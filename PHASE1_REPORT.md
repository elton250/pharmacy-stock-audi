# Phase 1 Completion Report — Pharmacy Stock Auditing System

Date: 2026-10-18 · Branch: n/a (local, not a git repo) · Scope: **Phase 1 only** (per master prompt; Phases 2–4 not started)

## 1. Features delivered

### Authentication & RBAC
- Initial-administrator setup flow (`GET /api/auth/setup-available`, `POST /api/auth/setup`); seed roles: `administrator`, `pharmacist`, `stock_counter`.
- Login/logout with argon2 password hashing and **server-side sessions** (SHA-256 token hash in `user_sessions`), cookies `pharmacy_session` (HttpOnly) + `pharmacy_csrf` (readable), `X-CSRF-Token` header enforced on mutating methods.
- Login rate limiting: 5 failed attempts / 15 minutes (per-process).
- Permission-based authorization on every endpoint (`require_permission`); route guards and permission-gated nav in the UI.
- User management: create, list, search, edit (roles, active flag, password reset); users can never lock out the last active administrator.

### Medicines, barcodes, packages
- Medicine CRUD with opening stock, reorder level, deactivate (soft).
- Barcode registration (primary barcode per medicine), **barcode-first scan** endpoint: known codes resolve to the medicine, unknown codes are never auto-assigned.
- Package configuration with `parent_id` + `units_per_parent`; `units_in_base` computed for top-level packages; package conversion used by counting.

### Inventory transactions
- Six transaction types (RECEIVED, ISSUED, RETURN_IN, RETURN_OUT, ADJUSTMENT_POSITIVE, ADJUSTMENT_NEGATIVE) with signed quantity, reference number, notes, recorder, timestamp.
- Expected stock always derived from transactions; opening stock locked after any other transaction (`opening_stock_locked`).

### Counting (strict static)
- Session lifecycle: DRAFT → IN_PROGRESS → PENDING_REVIEW → COMPLETED; rejects/cancels as specified; single active medicine per session.
- Count events with lines (package + quantity + `partial_units`), base-unit conversion, `idempotency_key` (globally unique; replay returns original event), `409 medicine_mismatch` on wrong-medicine attempts, `403 not_session_creator` for non-creators.
- Review by a user who is not the creator and not an operator of the session; decision `APPROVED`/`REJECTED`; approval records physical stock.
- **Session recovery:** `POST /sessions/{id}/resume` reuses the open session (no new session, no duplicated events, no reset) and `POST /sessions/{id}/cancel` is allowed for the creator or a `count:manage` supervisor, recording the actor. See section 6.

### Audits & reports
- Stock audit initiation with medicine scope, linking counting sessions, completion, cancellation.
- Variance report: opening/received/issued/adjustments → expected vs physical, variance, classification (SHORTAGE / SURPLUS / MATCH / UNCOUNTED), counting operator + reviewer + session code.
- Stock levels report with reorder flags; count-events report per session.
- **No automatic inventory adjustment anywhere** — variances are reported only.

### Audit logging
- Append-only `audit_logs` with action, entity type/id, actor, IP, user agent, before/after JSON for: auth events, user changes, medicine/barcode/package changes, transactions, session lifecycle, count events, reviews, cancel/resume, audit lifecycle.

### Frontend (React + TS)
- Login, Setup, Dashboard, Medicines (+ form), Scan (barcode-first), Counting workspace (sessions, package-line entry, submit/review, **active-session card with resume/cancel**), Inventory (transactions), Audits (initiate/link/complete with variance table), Reports (variance + stock levels), Users, Audit Log (expandable before/after diffs); auth-gated routes, permission-aware sidebar, toast/confirm dialogs, pagination.

## 2. Key files

| Area | Files |
|---|---|
| Backend core | `backend/app/{main,run,config,database,security,permissions,deps,audit,errors,cli}.py` |
| Models | `backend/app/models/{users,medicines,inventory,counting,audits,logs}.py` |
| Schemas | `backend/app/schemas/{users,medicines,inventory,counting,audits,common}.py` |
| Services | `backend/app/services/{stock,packages,counting,codes}.py` |
| Routers | `backend/app/routers/{auth,users,medicines,inventory,counting,counting_archive,audits,reports,audit_logs,meta}.py` |
| Migration | `backend/alembic/versions/{de59fc2743eb_initial_schema,0dcb2a75fe7b_add_users_must_change_password,312abc5d983f_counting_sessions_archive_fields_and_,5c9d1b7ae2f4_counting_sessions_single_active_index,b71f4a92c308_add_counting_sessions_cancelled_by}.py` |
| Startup | `scripts/start-dev.sh`, `scripts/check-health.sh` |
| Tests | `backend/tests/conftest.py` + 14 test files (auth, rbac, medicines, barcodes, packages, inventory, counting, single_session, archiving, counting_recovery, variance, startup, audit_logs, cli_admin) |
| Frontend | `frontend/src/{App.tsx,main.tsx,styles.css}`, `lib/{api,types}.ts`, `context/AuthContext.tsx`, `components/{Layout,ui}.tsx`, `pages/*` (13 pages), `vite.config.ts` |
| Docs | `README.md`, `PHASE1_REPORT.md`, `.env`, `.env.example` |

## 3. Migration status

- **Alembic migration `de59fc2743eb "initial schema"` generated and applied** to `pharmacy` — 18 tables verified (`alembic_version`, users, roles, user_roles, permissions, role_permissions, user_sessions, medicines, medicine_barcodes, package_configurations, inventory_transactions, counting_sessions, count_events, count_event_lines, stock_audits, stock_audit_items, audit_logs, + one more).
- `0dcb2a75fe7b` — `users.must_change_password`.
- `312abc5d983f` — counting-session archive fields (`archived_at`, `archived_by`, `archive_reason`) + `ix_counting_sessions_created_by_status`.
- `5c9d1b7ae2f4` — `uq_counting_sessions_single_active`, the partial unique index that enforces "one active counting session".
- `b71f4a92c308` — `counting_sessions.cancelled_by` (nullable BigInteger) + FK `fk_counting_sessions_cancelled_by_users` → `users.id`; additive migration for the session-recovery work (section 6). No existing row is modified.
- `alembic upgrade head` is the only migration step required for a fresh install; the full chain was verified from scratch on a throw-away database and `alembic check` reports no pending operations.

## 4. Test results

- **Backend: `147 passed, 2 warnings in 46.58s`** (`.venv/bin/python -m pytest -q`) — 116 original + 31 new session-recovery tests (section 6.3).
- Warnings are upstream deprecations only (Starlette TestClient/httpx).
- **Frontend: `npm run build` (tsc -b && vite build) passes cleanly**; `npm run lint` exits 0.
- Manual end-to-end verification (curl against running backend, live servers): setup → login (admin/pharmacist/stock_counter) → RBAC 403 for counter on medicine create → medicine + Box(10)/Blister(4) packages + EAN-13 barcode → scan resolves (unknown barcode → found:false) → transactions (opening 500 +200 −464 = **expected 236**) → audit created → counting session linked to audit → `medicine_mismatch` 409 → count **23 boxes + 1 blister + 2 loose = 236.000** → idempotency replay returns same event → submit → creator self-review 403 → reviewer `APPROVED` → session COMPLETED → variance report: expected 236 / physical 236 / variance 0 / **MATCH** with operator + reviewer → audit completed → audit log entries for every action.
- Startup recovery verified by stopping every layer and starting it again in order: frontend-only reproduced the reported **502** on `/api/*`; after `scripts/start-dev.sh` the proxy answers **200** and `/api/health` reports `{"status":"ok","database":"up"}`.

## 5. Phase 1 stabilisation fixes (2026-10-21)

Three production issues were reported and fixed; existing data was preserved (backup in
`backups/pharmacy_pre_fix_20261021_095553.dump`).

| Issue | Root cause | Fix |
|---|---|---|
| **502 after reboot** | PostgreSQL and the API were never started after a reboot; the Vite dev server still proxies `/api` to the dead backend and answers 502. The old start command also pointed the PostgreSQL socket at a non-existent `/tmp` directory, so the database refused to start. | `scripts/start-dev.sh` starts PostgreSQL → backend → frontend in order, waits for `/api/health`, and keeps every socket/log inside `runtime/`. `scripts/check-health.sh` reports which layer is down. `/api/health` now returns 503 with a reason when the database is unreachable, and the backend refuses to start without a database. |
| **Several counting sessions at once** | Nothing stopped a second session from being created while one was open. | API check (`409 active_session_exists`) **and** a PostgreSQL partial unique index `uq_counting_sessions_single_active` over `DRAFT`/`IN_PROGRESS`/`PENDING_REVIEW` — double clicks, extra tabs and concurrent requests can no longer create a second active session. The medicine of a session stays immutable. |
| **Cancelled / duplicate sessions** | Cancelled sessions stayed in the normal list and every completed session of a medicine was summed into physical stock. | Cancelled sessions contribute nothing to physical stock or variance; a re-count supersedes the previous count instead of adding to it; cancelled/draft sessions can be archived (soft delete, `count:manage` permission, audit-logged) and restored — nothing is ever hard-deleted. |

## 6. Session recovery (2026-10-21)

A fourth production issue was reported: an operator could get stuck. A session left open
(e.g. the operator closed the browser, or the machine was shut down) kept the single-active-session
rule occupied, so **nobody could start any new counting** and the operator could not see or continue
their own saved work from the UI.

### 6.1 What was added

| Area | Change |
|---|---|
| API | `POST /api/counting/sessions/{id}/resume` — reopens **the same** session: no new session row, no duplicated count events, no inventory transactions, no reset quantities. Returns the session with its saved events and progress. Audited as `counting.session_resume`. |
| API | `POST /api/counting/sessions/{id}/cancel` rewritten — authorisation widened from *creator only* to **creator OR holder of `count:manage`** (supervisor can release a session whose operator is unavailable). Unrelated operators get `403 forbidden`. Optional `reason` (≤ 500 chars) is collected in the confirmation dialog and stored in the audit log. The actor is persisted in the new column `counting_sessions.cancelled_by`. |
| API | `CountingSessionOut` gained `active_medicine_barcode`, `cancelled_by`, `cancelled_by_username` so the UI can show *which* medicine/barcode/session is in the way and *who* cancelled it. |
| DB | New migration `b71f4a92c308` — nullable `counting_sessions.cancelled_by` (BigInteger) + FK `fk_counting_sessions_cancelled_by_users` → `users.id`. Additive only; no existing row was modified. |
| Concurrency | `pg_advisory_xact_lock` (`ACTIVE_SESSION_LOCK_KEY`) serialises the check-then-act window in create / cancel / resume. Lock order is always **advisory → row** so the three endpoints cannot deadlock. The partial unique index remains the authoritative backstop. |
| Frontend | New **Active counting session** card on the counting page: session ID, session code, medicine, barcode, start time + operator, count progress, plus **Resume session** and **Cancel session** (with confirmation dialog). Resume/Cancel buttons in the session table. DRAFT sessions now offer a medicine picker instead of a dead end. |
| Frontend | `api.ts` maps a dead/unreachable backend to a readable `backend_unavailable` message instead of a generic network error; per-session unsaved draft lines are preserved when switching between sessions. |
| Permissions | `count:manage` description updated to *"Archive, cancel or remove accidental/duplicate counting sessions"* (descriptions are now kept in sync on startup). |

### 6.2 Behaviour rules

- **Resume** is allowed for the session creator or a holder of `count:manage` / `count:review`; it does **not** change the status (a `PENDING_REVIEW` session stays pending review) and it never produces a second active session.
- **Cancel** is atomic: status → `CANCELLED`, `cancelled_at`, `cancelled_by` and the `counting.cancel` audit entry are written in one transaction, so the single-active-session rule is released only after the cancellation really succeeded.
- **Cancelled sessions never count**: they contribute nothing to physical stock, variance, shortage/surplus, audit summaries or the stock-level report (`physical_stock_from_completed_counts` only reads `COMPLETED`, non-archived sessions).
- Error contract: `404 not_found`, `409 archived` / `already_completed` / `already_cancelled` / `invalid_state` / `active_session_exists`, `403 forbidden` — each mapped to a human-readable message in the UI.

### 6.3 Verification

- **Backend: `147 passed, 2 warnings`** (`.venv/bin/python -m pytest -q`), of which **31** are new in `backend/tests/test_counting_recovery.py` (resume reuse, no duplicated events/transactions, resume/cancel authorisation matrix, error codes, audit entries incl. the optional cancel reason, cancelled-session exclusion from variance and audit summary, advisory-lock mutual exclusion, single-active-row after resume).
- `alembic upgrade head` and `alembic check` → no pending operations; live database at revision `b71f4a92c308 (head)`.
- **Frontend: `npm run build` clean; `npm run lint` exits 0** (only pre-existing `oxlint` warnings).
- **Live end-to-end** against the running stack: the stuck session `CS-20261018-S9JHC5` (ID 1, `IN_PROGRESS` since 2026-10-18) was resumed, then cancelled by the administrator through the new endpoint — `cancelled_by = admin`, audit entries `counting.session_resume` and `counting.cancel` written. A new session could then be created immediately, and the variance report still showed the *completed* sessions (not the cancelled one) as the source of physical stock. All live data (medicines, users, permissions, count events, transactions, audit history) was preserved.
- A second live pass confirmed the full authorisation matrix against the running API: the administrator (holder of `count:manage`) cancelled a session created by another operator (`HTTP 200`, `cancelled_by_username = admin`); a second, unrelated `stock_counter` got `403 forbidden` for both cancel and resume while the target session stayed `IN_PROGRESS` and kept blocking new counting (`409 active_session_exists`); a cancel with `reason` persisted the reason verbatim in the `after_data` of the `counting.cancel` audit entry.
- The throwaway sessions, users, sessions and audit rows created during those live checks were removed afterwards. The live database was returned to its previous row counts (4 users, 3 medicines, 3 count events, 6 inventory transactions, 6 counting sessions, 91 pre-existing audit rows) — the only intentional change is session 1 now being `CANCELLED`, with its two truthful audit entries restored.

## 7. Known bugs / issues

- None open. Issues found and fixed during Phase 1: ambiguous `User.roles` FK join; missing imports (`DateTime`, `get_db`); deadlocked test teardown (truncate moved to fixture setup only); Decimal-as-string comparisons in tests; React fragment key warning in AuditLogs; frontend `active_medicine` field gap in counting sessions (resolved by fetching the medicine client-side in `Counting.tsx`).
- Login rate limiting is **per-process** (in-memory) — resets on restart and does not share state across workers (single-worker dev run is fine).
- The services are **not** registered as systemd units: after a reboot the operator must run `scripts/start-dev.sh` (automatic boot start needs operator approval).
- A full physical reboot could not be exercised in the test environment — recovery was verified by stopping and restarting every layer in the same order.
- The frontend has **no automated test runner** (no vitest/jest): UI behaviour is covered indirectly by the 144 backend tests plus a clean `npm run build` / `npm run lint`; a component-level test setup is deferred to a later phase.
- The administrator password of the live dev database is unknown to the development process and was never reset, so the live checks were performed with short-lived server-side sessions that were revoked and deleted afterwards.

## 8. Exact commands (copy-paste)

```bash
# Everything (PostgreSQL → backend → frontend), after a reboot:
scripts/start-dev.sh
scripts/check-health.sh      # diagnose a 502

# Database only (user-owned instance, port 54329)
pg_ctl -D ~/.local/share/pharmacy-postgres \
  -o "-p 54329 -k $PWD/runtime/pgsock" -l $PWD/runtime/postgres.log start

# Backend
cd backend
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python run.py --port 8000

# Frontend
cd frontend
npm install
npm run dev          # http://localhost:5173

# Tests
cd backend && .venv/bin/python -m pytest -q    # 147 passed
cd frontend && npm run build                   # clean
```

## 9. Deferred features (later phases — awaiting authorization)

Per the master prompt, Phases 2–4 were **not** implemented. Deferred, not started:

- Phase 2: batch/expiry tracking UI + enforcement, partial-package (cut) inventory effects, adjustments workflow with approval, dashboard analytics/aging.
- Phase 3: multi-item/bulk count UX, mobile offline counting, label printing, notification/escalation, performance tuning.
- Phase 4: hardening (refresh rotation, session revocation UI, rate-limit backend), localization (en + `es_MX`), deployment/compose, backup/restore, smoke/E2E test suites, production seeding.

**No Phase 2+ code paths were added.** `requires_batch` / `requires_expiry` columns exist on `medicines` but nothing consumes them yet — deferred by design.
