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
6. only then is the file published with an atomic no-clobber hard link
   (mode `0600`), retrying with a `_N` suffix instead of overwriting;
7. on any failure the temporary file is removed and the script exits non-zero,
   so a caller cannot mistake failure for success.

A regression test locks this in:
`backend/tests/test_ops_scripts.py::test_failed_backup_leaves_no_file_behind`.

### 2.2 Output layout and permissions

```
backups/                                   # mode 0700
├── pharmacy_pre_fix_20261021_095349.dump  # legacy 0-byte file, historical evidence, DO NOT USE
├── pharmacy_pre_fix_20261021_095553.dump  # legacy manual dump (71 320 bytes)
├── pharmacy_backup_20261022_133213.dump   # produced by scripts/backup-db.sh (mode 0600)
└── nightly/                               # scripts/nightly-backup.sh (0700)
    ├── pharmacy_nightly_<ts>.dump         # 0600, _1/_2 suffix on collisions
    ├── pharmacy_nightly_<ts>.dump.sha256  # 0600
    ├── pharmacy_nightly_<ts>.dump.manifest # 0600
    ├── logs/                              # 0700
    └── status/                            # 0700, last-success / last-failure / last-alert-failure
```

* directory `0700`, every dump and sidecar `0600` (`umask 077` is set in the
  scripts);
* a dump filename is never silently overwritten: publication uses an atomic
  hard link and retries with `_1`, `_2`, ... if the timestamp name or its
  metadata already exists;
* a dump is eligible only when the dump, `.sha256` and `.manifest` all exist;
  the manifest is published last, so an interrupted publication is treated as
  incomplete, never as a verified backup;
* the dumps contain every audit row, including usernames — treat them as
  confidential patient/operational data;
* the database password is read from `backend/.env`, passed through
  `PGPASSWORD`, and is never echoed, logged or embedded in a dump.

### 2.3 Retention

`scripts/backup-db.sh` prunes only dumps matching the same **database and
label** (`${database}_${label}_*.dump`) that are older than
`--retention-days` (default **30**), while always keeping the `--keep` newest
dumps (default **10**). Evidence such as `pharmacy_pre_fix_*.dump`, test
dumps, and quarantine files is never matched.

### 2.4 Nightly automation (not installed by Git)

`scripts/nightly-backup.sh` wraps the validated backup with a lock, private
logs, checksum/manifest verification, retention, and status files.
`scripts/verify-backup.sh` checks the newest dump and can restore it into a
disposable database without reading the live source.

```bash
scripts/nightly-backup.sh --dry-run          # no files, dumps or DB access
scripts/verify-backup.sh --latest-nightly --check-only
```

The intended cron entries live in `deploy/cron/pharmacy-backup.cron`:
nightly backup at **02:17** and a Sunday **03:17** restore verification.
Installation is a separate, explicitly approved step (`crontab
deploy/cron/pharmacy-backup.cron`). Status is reported by
`scripts/check-health.sh`; there is currently no email path, so
`PHARMACY_ALERT_CMD` is the alert hook to configure.

Backup success and alert success are recorded separately. A failed alert command
does not rewrite or remove the backup result: a successful backup keeps
`last-success`, and the alert failure is written to
`status/last-alert-failure` with `backup_status=success|failure` and
`alert_rc=<n>` (no alert output or secrets are stored). A later successful
alert delivery clears that alert-failure record. `check-health.sh` goes red
when `last-failure` is newer than `last-success`, and also when an unresolved
`last-alert-failure` exists.

#### Optional encrypted off-host upload

`scripts/nightly-backup.sh` supports an opt-in off-host copy after the local
dump, checksum, and manifest have been verified. Configure
`PHARMACY_OFFSITE_REMOTE` to a destination beginning with `pharmacycrypt:`
and ensure `rclone` is installed and authenticated for that encrypted remote.
For example, `PHARMACY_OFFSITE_REMOTE=pharmacycrypt:nightly` uploads the dump
and its `.sha256` and `.manifest` sidecars under that remote path. Plain remotes
such as `gdrive:` are deliberately rejected. `PHARMACY_RCLONE_BIN` can specify
the executable path when `rclone` is not on `PATH`.

The feature is disabled when `PHARMACY_OFFSITE_REMOTE` is unset. Dry-run never
calls `rclone`. If any off-site upload fails, the run fails and does not write
a new `last-success`; check `last-failure` and the private log. Keep OAuth and
crypt passwords only in the protected rclone configuration—never in source,
cron lines, or command arguments. The cron template is not installed by Git,
and enabling this option does not install or schedule cron automatically.

An encrypted off-host upload was manually tested separately, but this optional
nightly integration has only been verified with offline fake-rclone tests so far.
Keep the R7 off-host acceptance item open until the scheduled workflow and its
download/checksum/restore drill are verified end to end.

Keep at least one off-host copy (the backup directory lives on the same disk
as the database; disk failure destroys both).

---

## 3. Restoring

### 3.1 The automated drill (safe, non-destructive)

`scripts/restore-db.sh` **never** restores over a live database. It restores
into a newly created disposable database, or an existing empty drill database
only with `--allow-existing --keep`. A database created by the run is dropped
unless `--keep` is used; a pre-existing database is never dropped by this
script. By default it does **not** query source data: it verifies that the
restored copy is readable, that every public table can be counted, and that
`alembic_version` is present.

```bash
scripts/restore-db.sh --drill backups/pharmacy_backup_20261022_133213.dump
scripts/restore-db.sh --drill --keep backups/....dump   # keep the drilled DB for inspection
scripts/restore-db.sh --drill --no-live-compare backups/pharmacy_nightly_<ts>.dump
```

`--no-live-compare` is the default and is explicit here for clarity.
`scripts/verify-backup.sh` uses this mode for nightly verification.

A source-vs-restore row-count comparison is **opt-in only** and requires
explicit approval. It needs both `--compare-source` and
`PHARMACY_ALLOW_SOURCE_COMPARE=1`; without the env token the script refuses
before creating the temporary database.

```bash
PHARMACY_ALLOW_SOURCE_COMPARE=1 scripts/restore-db.sh --drill --compare-source \
  --key-tables users,medicines,inventory_transactions,stock_audits,audit_logs \
  backups/pharmacy_nightly_<ts>.dump
```

Four safeguards prevent a meaningless comparison:

* if the dump has a `.manifest`, its `database=` value must match the database
  in `DATABASE_URL`; a mismatch aborts **before** the temporary database is
  created;
* an unreadable or empty source result is a failure, not a false “match”;
* `--key-tables` fails if any named table is absent from either side;
* tables present only in the restored copy are reported as
  `DIFF ... source=<missing>` and fail the comparison.

A database created by the run is dropped by an `EXIT` trap on success, on a
row-count difference, on a missing key table, and on restore failure. A
pre-existing target selected with `--allow-existing` is only ever kept, never
dropped.

Safety interlocks (all enforced in code, not by convention):

* `--drill` is mandatory — without it the script refuses to do anything;
* the target database name is generated (`pharmacy_restore_drill_<ts>`) and the
  script aborts if it would ever equal the live database name;
* an existing target database is refused unless `--allow-existing --keep` is
  given; it is accepted only when the current role owns it and it contains no
  user relations, extra schemas, or non-default extensions; `pharmacy` and the
  configured source database are always refused as targets;
* an empty or unreadable dump is refused before any database is touched;
* source data is not queried by default; with `--compare-source` PostgreSQL is
  contacted only for **read-only** source row counts — never written, dropped
  or truncated;
* cleanup drops only a database created by the current run, including when the
  restore fails midway. If that drop fails, the script exits non-zero with a
  warning; when a restore/verification error already occurred, the original
  failure is preserved instead of being masked by the cleanup error.

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
   scripts/restore-db.sh --drill backups/<file>.dump
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

---

## 6. R7 open gaps (not closed by this automation)

These are tracked as open acceptance items, not as completed work:

- **Off-host copy:** no dump is copied off this machine. Backups and the
  database share one disk, so disk loss destroys both. A nightly off-host
  copy and restore test are still required.
- **Alert delivery:** there is no working mail path on the host. Failures are
  written to `backups/nightly/status/last-failure`, the private log, and
  optionally `PHARMACY_ALERT_CMD`. Alert-command failures are now recorded in
  `status/last-alert-failure`, but there is still no verified end-to-end
  delivery channel or escalation test.
- **Legacy dump validation:** existing `pharmacy_pre_fix_*` dumps have no
  `.sha256`/`.manifest`; the current scripts warn but cannot prove their
  source database or integrity. They need a one-time drill before anyone
  relies on them.
- **Source-vs-restore comparison:** disabled by default. A row-count
  comparison against the source requires explicit approval, `--compare-source`
  and `PHARMACY_ALLOW_SOURCE_COMPARE=1`; it is not part of nightly automation.
  It is a public-base-table row-count check, not a full schema/content diff.
- **Pre-existing drill targets:** `--allow-existing` checks ownership, user
  relations, extra schemas and non-default extensions, but not every possible
  schema object (for example standalone functions or domains). It remains a
  narrowly gated, empty-test-database convenience, not a general reuse mode.
