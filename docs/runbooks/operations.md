# Operations — Single-Server Resilience

This runbook covers the single-server deployment concerns from ADR-013 (deployment topology):
the Compose resource budget, the off-server encrypted backup, and the restore rehearsal. It is
repo-side configuration and scripts only — buying, provisioning, DNS, TLS, and the real deploy
are out of scope.

## Resource budget

Every Compose service carries an explicit CPU ceiling (`cpus`), memory ceiling (`mem_limit`),
and relative CPU weight (`cpu_shares`). Ceilings cap a single service's burst; weights decide
who yields when the box is saturated. Priority order is **postgres > api > redis > workers**.
See the header comment in [`compose.yaml`](../../compose.yaml) for the per-service justification
and ADR-013 for the full rationale.

Inspect the effective limits:

```powershell
docker compose config
```

Verify the API stays responsive while a worker job runs (acceptance check 2). With the stack and
the `worker` profile up, drive a media-analysis job, then poll readiness under load:

```powershell
docker compose --profile worker up -d
1..40 | ForEach-Object { (Invoke-WebRequest http://localhost:8000/health/ready -UseBasicParsing).StatusCode; Start-Sleep -Milliseconds 250 }
```

`/health/ready` must keep returning `200` with acceptable latency: the API sits just below
PostgreSQL and well above the workers in CPU weight, and the worker process renices itself
(`os.nice(+10)`) so its FFmpeg children inherit a lower priority.

### Worker scratch

The worker enforces a soft scratch budget (`WORKER_SCRATCH_BUDGET_EXCEEDED`) at 3/4 of the
`/tmp` tmpfs, below the hard `tmpfs size=512m` wall. A job that outruns the soft check hits
`ENOSPC` and fails through the normal error path — the disk never silently fills. Orphaned
scratch from a killed worker is reclaimed at process init.

### Restart / OOM-loop protection

`celery-worker` and `celery-beat` use `restart: on-failure`: a clean SIGTERM shutdown (exit 0)
stays down, and only a crash (e.g. OOM-kill) restarts, rate-capped by Docker's exponential
backoff so an OOM loop cannot busy-spin the CPU. Plain `docker compose` has no attempt cap; on
the production host bound it with a systemd drop-in on the Compose unit:

```ini
# /etc/systemd/system/socialpilot.service.d/restart.conf
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=5
```

## Encrypted off-server backup

`make backup` runs [`scripts/backup_db.py`](../../services/api/scripts/backup_db.py): it dumps
PostgreSQL (plain SQL), scans the dump for plaintext OAuth tokens, gzips, encrypts with
`openssl` AES-256-CBC + PBKDF2, uploads the ciphertext to object storage under a dated key, and
prunes old backups by retention. **No copy is left on the server's own disk.**

### Prerequisites on the backup host

`pg_dump` (postgresql-client) and `openssl`. The stateless API image ships neither; run the
backup where the database client and openssl exist (the single server itself, or a DB-adjacent
runner). Configure the `BACKUP_*` and `S3_*` variables from [`.env.example`](../../.env.example).
`BACKUP_ENCRYPTION_KEY` must come from the server's secret manager — never git.

### Run

```powershell
make backup
```

A success prints `db_backup_succeeded` (object key, size, sha256, pruned count); any failure
prints `db_backup_failed` with an `error_code` and exits non-zero so a scheduler surfaces it.

### Schedule (daily, off-server)

On the production host, a cron entry running as the deploy user (env sourced from the secret
manager, not committed):

```cron
17 2 * * *  cd /opt/socialpilot && make backup >> /var/log/socialpilot/backup.log 2>&1
```

## Restore rehearsal — an untested backup is not a backup

`make restore-check` runs [`scripts/restore_check.py`](../../services/api/scripts/restore_check.py):
it downloads the newest backup, decrypts and decompresses it, loads it into
`RESTORE_CHECK_DATABASE_URL` (a **throwaway** database — never production), then asserts the
restored Alembic head matches the code head and reports core-table row counts.

Prerequisites: `psql` and `openssl`. Create an empty scratch database first, then:

```powershell
make restore-check
```

Success prints `db_restore_check_succeeded` with the restored head and row counts; a mismatch
prints `db_restore_check_failed` with an `error_code` (e.g. `RESTORE_HEAD_MISMATCH`) and exits
non-zero. Run it on a schedule too — a backup pipeline that is never restored is not proven.

## Verifying the whole loop locally (MinIO stands in for R2)

With the default Compose stack up (MinIO provides the `S3_*` target), set the `BACKUP_*` and
`RESTORE_CHECK_*` variables and run `make backup` then `make restore-check` from inside a
container that has `pg_dump`/`psql`/`openssl` (the `postgres` image carries the client tools and
openssl). This exercises dump → scan → encrypt → upload → download → decrypt → restore → head
check end to end against real PostgreSQL and object storage.
