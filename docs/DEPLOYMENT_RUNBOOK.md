# Deployment & Rollback Runbook

**Status: written and rehearsed against the isolated test stack only.
No migration has ever been applied to the live `pharmacy` database by this
runbook, and no deployment has been performed in production.**

---

## 1. Current deployment model

There is no CI/CD, no container image, no systemd unit and no reverse proxy
configuration in this repository. The application is a two-process stack that
is started by hand:

| Component | Process | Port | Notes |
|---|---|---|---|
| PostgreSQL | `pg_ctl` on `$HOME/.local/share/pharmacy-postgres` | 54329 | project-local socket in `runtime/pgsock` |
| Backend | `backend/run.py` (uvicorn) | 8000 | Vite proxies `/api` → 8000 |
| Frontend | `npm run dev` (Vite) | 5173 | dev server, not a production build |

`scripts/start-dev.sh` is a **development** helper. It is idempotent and it
starts the layers in the correct order (PostgreSQL → backend → frontend), but
it has two properties that make it unsuitable as a production deploy tool:

* it runs `alembic upgrade head` automatically on every backend start
  (line 109) — a restart would silently migrate the database;
* its `--stop` uses `pkill -f` on project-scoped patterns (lines 167–168),
  which kills *every* matching process, including a manually started one.

For a real deployment, run the steps below explicitly instead of relying on
`start-dev.sh`.

---

## 2. Pre-deployment checklist

Nothing below may be skipped. Every item is either verified by a command or
by a human check.

**Scope & preparation**

- [ ] Change is scoped: list the files, the migrations and the configuration
      variables it touches.
- [ ] Tests pass on the target code:
      `cd backend && python -m pytest` and `cd frontend && npm run build`.
- [ ] A reviewer other than the author has read the diff.

**Data safety**

- [ ] Fresh, **validated** backup exists:
      `scripts/backup-db.sh --label pre-deploy-<ticket>` → note the reported
      size and sha256. A dump of 0 bytes is a failed backup — the script now
      refuses to publish one, but verify the reported size anyway.
- [ ] The backup file is ≥ 1 KiB and `pg_restore --list` succeeds on it.
- [ ] A restore drill has been run against the same dump **in this release
      cycle** (see `docs/BACKUP_RESTORE.md` § 3.1) — the drill restores into a
      disposable database and drops it again.
- [ ] The `SECRET_KEY` of the live deployment is known to be restorable
      (it is *not* in the backup).

**Migrations**

- [ ] `alembic history` reviewed: every new revision is understood, including
      whether it is reversible (see § 6).
- [ ] The migration has been applied to a database that is a copy of
      production data, not only to an empty test database.
- [ ] The application code works with **both** the pre- and the post-migration
      schema during the deploy window (no in-place incompatible change).
- [ ] A tested `alembic downgrade` path exists for every new revision, **or**
      the revision is explicitly marked irreversible and a restore-from-backup
      rollback has been planned for it.

**Operations**

- [ ] Maintenance window agreed (a migration that takes a lock needs the
      backend stopped).
- [ ] On-call person is present and knows the rollback trigger criteria (§ 5).
- [ ] `scripts/check-health.sh` currently reports all layers `[ OK ]`
      (a green baseline is required to detect a regression).

---

## 3. Deployment procedure

```
1. STOP THE WRITERS        (backend down, PostgreSQL stays up)
2. BACK UP                  scripts/backup-db.sh --label pre-deploy-<ticket>
3. APPLY MIGRATIONS         python -m alembic upgrade head
4. DEPLOY THE CODE          (checkout / copy the reviewed revision)
5. START THE BACKEND        python run.py --host 127.0.0.1 --port 8000
6. HEALTH CHECKS            scripts/check-health.sh  + the API/UI checks below
7. START THE FRONTEND       npm run dev -- --host 127.0.0.1 --port 5173
8. SMOKE TEST               login → medicine search → count session → audit
9. ANNOUNCE                 note the backup file, the revision, the time
```

Concrete commands:

```bash
cd /path/to/pharmacy-stock-audit

# 1. stop the writers (backend only)
pkill -f 'run[.]py --host 127[.]0[.]0[.]1 --port 8000'
pgrep -af 'run[.]py' || echo "no backend process left"

# 2. validated backup
scripts/backup-db.sh --label pre-deploy-TICKET-123

# 3. migrations (test first on a copy!)
cd backend && python -m alembic upgrade head && cd ..

# 4. deploy the code (git checkout / rsync of the reviewed revision)
git checkout <reviewed-revision>

# 5. start the backend (no --reload in production)
cd backend && nohup python run.py --host 127.0.0.1 --port 8000 >>../runtime/logs/backend.log 2>&1 &

# 6. health checks
scripts/check-health.sh
```

### 3.1 Health checks after deployment

```bash
scripts/check-health.sh
```

| Check | Expected |
|---|---|
| `PostgreSQL (127.0.0.1:54329)` | `[ OK ]` |
| `Backend /api/health` | `[ OK ]` (HTTP 200, body `"status":"ok"`) |
| `Frontend proxy /api/health` | `[ OK ]` (HTTP 200 — proves the Vite→backend proxy works) |
| `Frontend page` | `[ OK ]` (HTTP 200) |

Functional smoke test (as the admin user, in the UI):

1. log in → dashboard loads without a 5xx;
2. `Medicines` → list loads, search for a known medicine returns it;
3. open a medicine → barcode, packages and stock are rendered;
4. start a counting session → count a line → submit;
5. review the variance as a *different* user → the audit shows
   `counted_by` and `reviewed_by` correctly;
6. `Audit log` → the new entries are present with the expected actor.

```bash
# API-level equivalents
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:5173/api/health        # through the frontend proxy
```

**Rule:** no 5xx in `runtime/logs/backend.log` after the deployment timestamp.
A 4xx caused by an invalid request is acceptable; a 5xx is not.

---

## 4. Rollback criteria (when to roll back)

Roll back immediately, without further investigation, if **any** of these is
true after the deployment:

- `scripts/check-health.sh` reports a layer `[DOWN]` and the cause is not
  clearly unrelated to the change;
- `/api/health` returns 500, or the frontend proxy returns 502;
- the backend log contains a new 5xx or a repeated traceback after the
  deployment timestamp;
- login is broken, or the CSRF/cookie flow fails (this usually means
  `SECRET_KEY` changed);
- the smoke test fails at any step that passed before the deployment;
- the migration took a lock and did not finish in the expected time;
- row counts of the core tables changed unexpectedly
  (`users`, `medicines`, `medicines_barcodes`, `inventory_transactions`,
  `counting_sessions`, `count_event_lines`, `stock_audits`, `audit_logs`);
- a counting session cannot be created or reviewed (core business flow
  blocked);
- any data loss is suspected — stop and restore, do not attempt a partial fix.

**Do not** roll back for: cosmetic issues, slow-but-completing migrations
under an agreed threshold, or a single user's browser problem.

---

## 5. Rollback procedure

### 5.1 Code-only rollback (migration was reversible and already applied)

```bash
pkill -f 'run[.]py --host 127[.]0[.]0[.]1 --port 8000'
pgrep -af 'run[.]py' || echo "no backend process left"
git checkout <previous-reviewed-revision>
cd backend && python -m alembic downgrade <revision-before-the-change>
cd ..
python run.py --host 127.0.0.1 --port 8000 >>../runtime/logs/backend.log 2>&1 &
scripts/check-health.sh
```

Only valid when the downgrade is truly reversible (§ 6). The downgrade must be
executed **before** the old code is started if the old code expects the old
schema.

### 5.2 Data rollback (migration was irreversible or data was corrupted)

Use the backup taken in step 2 of the deployment. The full procedure —
including the `ALTER DATABASE ... RENAME` cutover — is in
`docs/BACKUP_RESTORE.md` § 3.2. It requires a downtime window of roughly
2–5 minutes and keeps the previous database (`pharmacy_old`) until the
incident is closed.

### 5.3 Rollback verification

Repeat § 3.1 (health checks + smoke test) and confirm that the row counts
match the pre-deployment numbers.

---

## 6. Alembic: what `downgrade` can and cannot do

All six revisions of this project are linear and each one defines a
`downgrade()`. That does **not** mean a downgrade is safe:

| Migration type | `alembic downgrade` reverses it? | Consequence |
|---|---|---|
| Add column / add table | **Yes** | data in the new column is dropped |
| Add index / add constraint | **Yes** | safe; only the constraint disappears |
| Rename column | Only if the `downgrade()` renames it back | a `downgrade()` that drops the column instead loses data |
| Drop column / drop table | **No** | the data is gone — only a restore can recover it |
| Data migration (`UPDATE`/`INSERT`) | **Usually no** | `downgrade()` rarely restores the previous values |
| Type change / narrowing | **No** | values may be truncated irreversibly |
| `NOT NULL` added on existing rows | **No** | rows that violate it cannot simply be re-inserted |

Additional limitations that apply to this project:

1. **The downgrade has never been executed.** The existing `downgrade()`
   implementations are unverified code paths — a downgrade can fail halfway
   and leave the schema in an intermediate state, which is *worse* than not
   downgrading at all.
2. **`alembic downgrade` is not atomic.** Each revision is applied in its own
   transaction; a failure in the middle leaves the earlier revisions already
   reverted.
3. **The application code must match the schema.** After a downgrade, the
   running code must be the code that expects the downgraded schema.
4. **`upgrade head` runs automatically in `scripts/start-dev.sh`.** A restart
   after a failed deployment will re-apply the migration that you are trying
   to escape from. For a rollback, downgrade *first*, then restart.
5. **No production migration has ever been run by this runbook.** The live
   database is at the revision `b71f4a92c308` (head as of 2026-10-22).
6. **The revision `c8f2a4b91d7e` (vision captures) was only applied to the
   test databases** (`pharmacy_test`, `pharmacy_test_pytest`). Its
   `downgrade()` drops a table that the live database does not contain yet —
   running it against the live database is a no-op, running `upgrade` is not.

**Rule:** if a new migration is not demonstrably reversible, plan the rollback
as a restore from backup (§ 5.2) from the start — do not improvise a
`downgrade()` during an incident.

---

## 7. Known gaps (not solved by this runbook)

- No CI pipeline: the test commands below are manual.
  ```bash
  cd backend  && PHARMACY_TEST_DB_URL=postgresql+psycopg://…/pharmacy_test_pytest python -m pytest
  cd frontend && npm test && npm run build && npm run lint
  ```
- No container/systemd definition: the processes are started by hand and
  survive only as long as the shell session does.
- No artefact versioning: a deployment is "the current working tree", so the
  rollback target is "whatever `git checkout` gives you" — pin a commit
  before deploying.
- The frontend is served by the Vite **dev** server; a production build
  (`npm run build`) is not served by anything yet.
- No alerting: the health checks are manual, so the RTO in
  `docs/BACKUP_RESTORE.md` assumes a human is watching.

---

## 8. Deploying the vision-assisted identification (Phase 2A)

The feature is **optional**: the backend runs without it, the manual entry
stays available and nothing else changes. It needs three things: one
migration, one optional Python package and a local OCR engine.

### 8.1 Order of operations

```
1. BACK UP                    scripts/backup-db.sh --label pre-deploy-vision
2. OPTIONAL DEPENDENCY        backend/.venv/bin/pip install -r backend/requirements-ocr.txt
3. OCR ENGINE                 install tesseract, set OCR_COMMAND / OCR_TESSDATA_DIR
4. MIGRATION                  backend/.venv/bin/python -m alembic upgrade head   (revision c8f2a4b91d7e)
5. DEPLOY + RESTART           backend/.venv/bin/python run.py --host 127.0.0.1 --port 8000
6. VERIFY                     curl /api/vision/status  + one upload in the UI
7. NIGHTLY CLEANUP            cron: prune-vision-captures (see 8.4)
```

The migration must run **before** the new backend is started. If the order is
reversed the endpoint answers `503 migration_pending` with an explanatory
message instead of failing with an opaque database error.

### 8.2 Configuration

| Variable | Meaning | Default |
|---|---|---|
| `VISION_ENABLED` | feature on/off | `true` |
| `VISION_CAPTURE_DIR` | where the photos are stored (not web-accessible) | `<project>/runtime/captures` |
| `VISION_MAX_UPLOAD_BYTES` | upload limit | `5242880` (5 MiB) |
| `VISION_MAX_PIXELS` | pixel bomb protection | `25000000` |
| `VISION_RETENTION_DAYS` | how long a photo is kept | `30` |
| `OCR_COMMAND` | path to the tesseract binary | `tesseract` |
| `OCR_TESSDATA_DIR` | directory with the language data | auto-detected |
| `OCR_LANGUAGE` | language(s) | `eng` |
| `OCR_TIMEOUT_SECONDS` | hard timeout for one recognition | `15` |

A root-free installation works as well:

```bash
OCR_COMMAND=$HOME/.local/opt/tesseract-5.5.0/usr/bin/tesseract
OCR_TESSDATA_DIR=$HOME/.local/opt/tesseract-5.5.0/usr/share/tesseract-ocr/5/tessdata
```

Without an OCR engine the upload answers `503 ocr_unavailable` and the manual
entry keeps working. Without Pillow it answers `503 image_support_unavailable`.

### 8.3 Data protection

* Photos are written with mode `0600` into a directory with mode `0700`; they
  are never served by the web server.
* The EXIF metadata is removed and the picture is re-encoded before it is
  stored — the original file never touches the disk.
* A photo is deleted as soon as the capture is confirmed or rejected.
* Nothing leaves the server: the recognition runs in the local tesseract
  process, no external service is contacted.
* The recognition never writes stock and never creates a medicine; a person
  confirms every result, and the decision is written to the audit log
  (`vision.capture_created`, `vision.capture_confirmed`, `vision.capture_rejected`).

### 8.4 Nightly cleanup

```cron
17 2 * * * cd /path/to/pharmacy-stock-audit/backend && \
  DATABASE_URL=… ./.venv/bin/python -m app.cli prune-vision-captures >> ../runtime/logs/vision-prune.log 2>&1
```

First run it with `--dry-run` to see what it would delete. The command deletes
the image files of captures that are older than the retention window, clears
the OCR text and the candidates and marks the capture as `expired`.

### 8.5 Verification after the deployment

```bash
curl -s -b cookies -H "X-CSRF-Token: $TOKEN" http://127.0.0.1:8000/api/vision/status
# {"enabled":true,"engine":"tesseract","engine_available":true, ...}
```

* `engine_available: true` → the OCR engine was found;
* upload one picture in the UI → the candidates appear, the result is confirmed
  by a person and the photo disappears from `runtime/captures`;
* `select count(*) from vision_captures;` → the new capture is there;
* `select action from audit_logs where action like 'vision.%';` → the decision
  is logged.

### 8.6 Rollback

The revision `c8f2a4b91d7e` only **adds** a table, therefore:

```bash
pkill -f 'run[.]py --host 127[.]0[.]0[.]1 --port 8000'
pgrep -af 'run[.]py' || echo "no backend process left"
cd backend && python -m alembic downgrade b71f4a92c308 && cd ..
git checkout <previous-reviewed-revision>
python run.py --host 127.0.0.1 --port 8000 >>../runtime/logs/backend.log 2>&1 &
```

The downgrade drops the table and with it the captures — the photos themselves
are removed by the downgrade path as well. If the photos must survive the
rollback, copy `runtime/captures` somewhere else before the downgrade.
