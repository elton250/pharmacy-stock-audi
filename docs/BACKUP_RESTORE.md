# Backup & Restore Runbook

Scope: the `pharmacy` PostgreSQL database (the only stateful component of this
application). Everything else — source code, frontend build, dependency
folders — is reproducible from the repository and is **not** covered by the
database backup.

---

## 1. What is (and is not) backed up

| Item | Backed up? | Notes |
|---|---|---|
| `pharmacy` database (all schema + data) | **Yes** | logical dump, custom format |
| `pharmacy_test` / `pharmacy_test_pytest` | No (by default) | only when `--database` is passed explicitly |
| PostgreSQL server installation / data directory | No | rebuilt by `scripts/start-dev.sh`; a logical dump is version-portable between the same major version |
| `backend/.env` (secrets, DSN, SECRET_KEY) | No | secrets are **never** written into a dump. Back it up separately, encrypted, outside this repository |
| `runtime/` logs | No | transient; they can contain usernames and audit details — archive separately if required by policy |
| `frontend/dist/` build output | No | reproducible: `npm run build` |
| `node_modules/`, `backend/.venv/` | No | reproducible from the lockfile / `requirements.txt` |

**Consequence:** a restore gives you the *data*, not the secrets. `SECRET_KEY`
in `backend/.env` must be restored from your secret store, otherwise every
existing session and CSRF token is invalidated.

---

## 2. Creating a backup

```bash
scripts/backup-db.sh                          # database from backend/.env DATABASE_URL
scripts/backup-db.sh --database pharmacy_test # a specific database
scripts/backup-db.sh --label pre-migration    # label embedded in the file name
scripts/backup-db.sh --retention-days 30 --keep 10
```

Environment overrides: `PHARMACY_BACKUP_DIR`, `PHARMACY_ENV_FILE`,
`PHARMACY_PG_BIN`.

### 2.1 Why the script exists (the 0-byte incident)

On 2026-10-21 a manual dump produced
`backups/pharmacy_pre_fix_20261021_095349.dump` with **0 bytes** and was
believed to be a valid backup. Cause: the shell redirection
`pg_dump ... > "$FILE"` creates the target file *before* `pg_dump` runs, and
`pg_dump` failed client-side (it never reached the server — PostgreSQL was up
and logged no connection error). Because neither the exit status nor the file
size was checked, a failed backup was indistinguishable from a successful one.

`scripts/backup-db.sh` makes that failure mode impossible:

1. `pg_dump` writes to a hidden temporary file (`.incomplete_*`), never to the
   final name;
2. the `pg_dump` exit status is checked (a non-zero status aborts the run);
3. the file must be **non-empty** and at least 1 KiB;
4. the archive is validated with `pg_restore --list` (the TOC must be
   readable and non-empty);
5. the sha256 checksum is computed and logged;
6. only then is the file published with an atomic `mv` (mode `0600`);
7. on any failure the temporary file is removed and the script exits non-zero,
   so a caller cannot mistake failure for success.

A regression test locks this in:
`backend/tests/test_ops_scripts.py::test_failed_backup_leaves_no_file_behind`.

### 2.2 Output layout and permissions

```
backups/                                   # mode 0700
├── pharmacy_pre_fix_20261021_095349.dump  # legacy 0-byte file, historical evidence, DO NOT USE
├── pharmacy_pre_fix_20261021_095553.dump  # legacy manual dump (71 320 bytes)
└── pharmacy_backup_20261022_133213.dump   # produced by scripts/backup-db.sh (mode 0600)
```

* directory `0700`, every dump `0600` (`umask 077` is set in the script);
* the dumps contain every audit row, including usernames — treat them as
  confidential patient/operational data;
* the database password is read from `backend/.env`, passed through
  `PGPASSWORD`, and is never echoed, logged or embedded in a dump.

### 2.3 Retention

`scripts/backup-db.sh` prunes dumps of the same database that are older than
`--retention-days` (default **30**), while always keeping the `--keep` newest
dumps (default **10**). Pruning only ever touches `*.dump` files in the
backup directory and never the newest *K* dumps, regardless of age.

Recommended schedule (nightly, off-peak):

```cron
17 2 * * *  cd /path/to/pharmacy-stock-audit && scripts/backup-db.sh --label nightly >> runtime/logs/backup.log 2>&1
```

Keep at least one off-host copy (the backup directory lives on the same disk
as the database; disk failure destroys both).

---

## 3. Restoring

### 3.1 The automated drill (safe, non-destructive)

`scripts/restore-db.sh` **never** restores over a live database. It restores
into a freshly created, disposable database, compares the row count of every
table against the source, prints a verdict, and drops the disposable
database again.

```bash
scripts/restore-db.sh --drill backups/pharmacy_backup_20261022_133213.dump
scripts/restore-db.sh --drill --keep backups/....dump   # keep the drilled DB for inspection
```

Safety interlocks (all enforced in code, not by convention):

* `--drill` is mandatory — without it the script refuses to do anything;
* the target database name is generated (`pharmacy_restore_drill_<ts>`) and the
  script aborts if it would ever equal the live database name;
* an existing target database is refused unless `--allow-existing` is given
  (and `pharmacy` is refused even then);
* an empty or unreadable dump is refused before any database is touched;
* the source database is only ever **read** (row counts) — never written,
  dropped or truncated;
* the disposable database is dropped automatically, including when the restore
  fails midway.

### 3.2 Real restore of the live database (deliberate, requires downtime)

The drill deliberately cannot do this. A real restore is a manual, explicit
procedure — plan a maintenance window and take a fresh backup first:

1. **Announce and stop the writers.** Stop the backend so no new
   transactions/audits are written:
   ```bash
   pkill -f 'run[.]py --host 127[.]0[.]0[.]1 --port 8000'   # live port; test stack uses 8001
   pgrep -af 'run[.]py' || echo "no backend process left"
   ```
2. **Take a safety dump of the current state** (so the cutover itself is
   reversible):
   ```bash
   scripts/backup-db.sh --label pre-restore
   ```
3. **Restore into a clean database** (never into a half-existing one):
   ```bash
   createdb -h 127.0.0.1 -p 54329 -U pharmacy pharmacy_restore
   pg_restore -h 127.0.0.1 -p 54329 -U pharmacy --no-owner --no-privileges \
       --exit-on-error -d pharmacy_restore backups/<file>.dump
   ```
4. **Verify** before cutting over — same check the drill performs:
   ```bash
   scripts/restore-db.sh --drill --allow-existing backups/<file>.dump
   ```
   plus a manual sanity check of row counts (`users`, `medicines`,
   `audit_logs`) and of `alembic_version` (it must equal the revision the
   deployed code expects).
5. **Cut over** — rename the databases (this is the only step that changes
   the live database):
   ```bash
   # stop the backend first, then:
   psql -d postgres -c "ALTER DATABASE pharmacy RENAME TO pharmacy_old;"
   psql -d postgres -c "ALTER DATABASE pharmacy_restore RENAME TO pharmacy;"
   ```
   `ALTER DATABASE ... RENAME` does not move the data files of an open
   connection, so the backend must be down.
6. **Start the backend** and run the health checks
   (`scripts/check-health.sh`, see `docs/DEPLOYMENT_RUNBOOK.md`).
7. **Keep `pharmacy_old` until the new deployment has been signed off** (at
   least 24 h), then drop it:
   ```bash
   dropdb -h 127.0.0.1 -p 54329 -U pharmacy pharmacy_old
   ```

---

## 4. Measured numbers and realistic RPO/RTO

Measured on the isolated test stack on 2026-10-22 (database ≈ 9.9 MB, logical
dump ≈ 72 KB compressed):

| Operation | Measured |
|---|---|
| `scripts/backup-db.sh` (full dump + validation) | **0.08 s** |
| `scripts/restore-db.sh --drill` (create + restore + verify every table + drop) | **0.79 s** |
| dump size | 72 KB (≈ 1:135 vs. the 9.9 MB database) |
| live `pharmacy` database size | 9.3 MB |

Extrapolation to the live database (same order of magnitude, 9.3 MB):
backup ≈ **< 1 s**, restore + verification ≈ **1–2 s**. The dominant cost of a
real restore is not `pg_restore` — it is the **stop/cutover/start window**.

### Realistic objectives

* **RPO (how much data can be lost):** equal to the backup interval. With the
  recommended nightly cron, RPO = **24 h**. Nothing in the application writes
  to a separate durable log, so the database dump is the only recovery point.
  If 24 h is too coarse, schedule the backup hourly (cost: < 1 s of CPU per
  run) — the script is safe to run concurrently with the application because
  `pg_dump` uses a consistent snapshot.
* **RTO (how long the service is down):**
  * *data loss event, database intact* → no downtime, restore is not needed;
  * *database corrupted / disk lost* → **≈ 2–5 min**: stop backend (10 s) +
    restore into `pharmacy_restore` (2 s) + cutover rename (5 s) + start
    backend (10 s) + health check (30 s) + smoke test (2 min).
  * *application rollback* → see `docs/DEPLOYMENT_RUNBOOK.md` (code-only
    rollback, ≈ 1 min, no data movement **if** the migration is reversible).

These numbers are measured on this hardware, not vendor guarantees. Re-measure
after every significant data-volume change and keep the drill in the release
checklist.

---

## 5. Verification checklist (run after every restore procedure)

- [ ] `scripts/check-health.sh` → all layers `[ OK ]`
- [ ] `GET /api/health` → HTTP 200
- [ ] login works with an existing user (proves `SECRET_KEY` + users were restored)
- [ ] row counts of `users`, `medicines`, `medicines_barcodes`,
      `inventory_transactions`, `counting_sessions`, `count_event_lines`,
      `stock_audits`, `audit_logs` match the pre-incident numbers
- [ ] `alembic_version` equals the revision the deployed code expects
- [ ] a read-only audit list loads and shows the expected entries
- [ ] the disposable drill database was dropped (`\l` in `psql`)
