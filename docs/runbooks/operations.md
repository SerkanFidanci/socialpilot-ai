# Operations — Single-Server Resilience

This runbook covers the single-server deployment concerns from ADR-013 (deployment topology):
the Compose resource budget, the off-server encrypted backup, and the restore rehearsal. It is
repo-side configuration and scripts only — buying, provisioning, DNS, TLS, and the real deploy
are out of scope.

## Resource budget

Every Compose service carries an explicit CPU ceiling (`cpus`), memory ceiling (`mem_limit`),
and relative CPU weight (`cpu_shares`). Ceilings cap a single service's burst; weights decide
who yields when the box is saturated. Priority order is **postgres > api > valkey > workers**,
with the backup runner in the lowest tier alongside beat. See the header comment in
[`compose.yaml`](../../compose.yaml) for the per-service justification and ADR-013 for the full
rationale.

| Service | `cpus` | `mem_limit` | `cpu_shares` |
|---|---|---|---|
| postgres | 2.0 | 8g | 2048 |
| api | 2.0 | 2g | 1024 |
| valkey | 0.5 | 512m | 512 |
| minio (dev only) | 1.0 | 1g | 512 |
| celery-worker | 2.0 | 4g | 256 |
| celery-beat | 0.25 | 128m | 128 |
| backup / restore-check | 1.0 | 512m | 128 |

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

### The runner (W06 — deployment gate D1)

ADR-013 wrote the procedure; [ADR-019](../adr/ADR-019-runtime-image-baseline-and-backup-runner.md)
gave it something that runs it. The `backup` Compose profile carries two **one-shot** services:

```bash
docker compose run --rm backup
```

```bash
docker compose run --rm restore-check
```

```bash
docker compose --profile backup up -d
```

The first takes one backup. The second takes a backup and then restores that backup — `run`
starts dependencies, and `restore-check` depends on `backup` completing successfully; add
`--no-deps` to rehearse whatever is already newest in storage instead. The third runs the same
loop detached; `-d` is required, because plain `up` also attaches to postgres and minio, which
never exit. Read the outcome with `docker compose logs backup restore-check`.

Both containers exit when the script does, and their exit status *is* the result. There is
deliberately **no always-on scheduler container**: on a single server that would duplicate the
host's own timer with worse failure semantics, and ADR-013 exists to keep resident processes off
this box.

### Prerequisites

`pg_dump`, `psql` and `openssl` — all three are in the `backup` build target
(`services/api/Dockerfile`), which is the API image plus `postgresql-client-18` from
PostgreSQL's own apt repository. The API image itself still ships none of them, and
`api`/`celery-worker`/`celery-beat` pin `target: runtime` so a Dockerfile edit cannot quietly
change that.

The client must not be older than the server: `pg_dump` refuses a server newer than itself, so a
Debian-supplied client 17 against the 18.4 server would abort with a version mismatch rather
than produce a smaller backup. When the server major version moves, the `postgresql-client-NN`
package in the Dockerfile moves with it.

Configure the `BACKUP_*` and `S3_*` variables from [`.env.example`](../../.env.example).
`BACKUP_ENCRYPTION_KEY` must come from the server's secret manager — never git, and never the
development default baked into `compose.yaml`.

A success prints `db_backup_succeeded` (object key, size, sha256, pruned count); any failure
prints `db_backup_failed` with an `error_code` and exits non-zero so a scheduler surfaces it.

### Schedule (daily, off-server)

A systemd timer, not a container loop — the exit status has somewhere to go. As the deploy user,
with the environment sourced from the secret manager rather than committed:

```ini
# /etc/systemd/system/socialpilot-backup.service
[Unit]
Description=SocialPilot encrypted off-server database backup
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/socialpilot
EnvironmentFile=/etc/socialpilot/backup.env
ExecStart=/usr/bin/docker compose run --rm backup
```

```ini
# /etc/systemd/system/socialpilot-backup.timer
[Unit]
Description=Daily SocialPilot backup

[Timer]
OnCalendar=*-*-* 02:17:00
RandomizedDelaySec=600
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl enable --now socialpilot-backup.timer
```

`Persistent=true` matters on a single server: a box that was down at 02:17 runs the missed
backup when it comes back rather than skipping the night. Add an `OnFailure=` unit pointing at
whatever alerting exists — a backup that fails silently is the same as no backup.

Schedule the rehearsal too, less often (weekly is enough), as a second timer running
`docker compose run --rm restore-check`.

A plain cron entry is an acceptable substitute where systemd is not available:

```cron
17 2 * * *  cd /opt/socialpilot && docker compose run --rm backup >> /var/log/socialpilot/backup.log 2>&1
```

`make backup` and `make restore-check` remain for running the scripts directly on a host that
already has the client tools; the Compose services are the packaged form of the same commands.

## Restore rehearsal — an untested backup is not a backup

[`scripts/restore_check.py`](../../services/api/scripts/restore_check.py) downloads the newest
backup, decrypts and decompresses it, loads it into `RESTORE_CHECK_DATABASE_URL` (a **throwaway**
database — never production), then asserts the restored Alembic head matches the code head and
reports core-table row counts.

The `restore-check` service recreates that scratch database before every run, so the rehearsal
is repeatable rather than failing the second time on objects that already exist. `DROP DATABASE`
sits in the Compose entrypoint, not behind a flag in the script, and the database **name** is a
literal there: only the server it is created on follows the DSN. A `--recreate` environment flag
would be a deletion verb that something could point at the wrong host at 02:17.

Counted tables are `businesses`, `media_assets`, `jobs`, `credit_ledger`, `usage_reservations`
and `entitlement_ledger_anchors`. The last three are there because they are what a restore
breaks most quietly: in a plain dump `usage_reservations` sorts *after* `credit_ledger`, and
migration `0020`'s insert guard rejects a ledger entry naming a reservation it cannot see. The
restore works because `pg_dump` emits triggers in the post-data section, after the COPYs —
counting these tables is how that stays a tested fact.

Success prints `db_restore_check_succeeded` with the restored head and row counts; a mismatch
prints `db_restore_check_failed` with an `error_code` (e.g. `RESTORE_HEAD_MISMATCH`) and exits
non-zero.

Row counts alone are not proof. After a restore that matters — an upgrade, a real recovery —
confirm the guards came back too, since they live in that same post-data section:

```bash
docker compose exec -T postgres psql -U socialpilot -d socialpilot -tAc \
  "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE c.relname = 'credit_ledger' AND NOT t.tgisinternal"
```

Two: `trg_credit_ledger_append_only` and `trg_credit_ledger_insert_guard`.

## Verifying the whole loop locally (MinIO stands in for R2)

With the default Compose stack up, MinIO provides the `S3_*` target and the backup profile needs
no further configuration:

```bash
docker compose --profile backup up -d
docker compose logs backup restore-check
```

This exercises dump → plaintext-token scan → gzip → encrypt → upload → download → decrypt →
restore → head check end to end against real PostgreSQL and real object storage. To confirm the
stored object is genuinely ciphertext rather than trusting the filename, its first eight bytes
are `Salted__` (openssl's `-salt` header) and it contains neither the gzip magic bytes nor any
readable SQL.

## Major-version upgrades

PostgreSQL major upgrades have their own procedure and their own trap (`PGDATA` moved in 18):
[postgres-major-upgrade.md](postgres-major-upgrade.md).
