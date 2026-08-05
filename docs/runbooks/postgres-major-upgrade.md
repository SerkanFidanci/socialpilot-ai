# PostgreSQL major-version upgrade (production)

Development does not need this: the volume is thrown away and the schema is rebuilt from the
`0001 → 0020` migration chain. Production cannot do that, and a major-version bump is **not** an
image bump — an 18 server refuses to start on a 17 data directory and says so, which is the good
case. The bad case is the one below under "The trap".

Scope: the single-server topology of [ADR-013](../adr/ADR-013-single-server-deployment-topology.md),
image line pinned by [ADR-019](../adr/ADR-019-runtime-image-baseline-and-backup-runner.md).
Buying, provisioning, DNS and TLS stay out of scope.

## The trap: PGDATA moved in 18

The official image changed `PGDATA` from `/var/lib/postgresql/data` to
`/var/lib/postgresql/18/docker`, and now declares the volume one level up at
`/var/lib/postgresql`. `compose.yaml` mounts the new path.

A volume still mounted at the old path against an 18 image **does not error**. The entrypoint
finds an empty `PGDATA`, runs `initdb`, and hands you a healthy, empty database — while the real
cluster sits untouched in the same volume one directory over. Every check that only asks "is
PostgreSQL up?" passes. Verify with the data, not the health probe:

```bash
psql "$DSN" -tAc "SELECT version()"
psql "$DSN" -tAc "SELECT version_num FROM alembic_version"
psql "$DSN" -tAc "SELECT count(*) FROM businesses"
```

## Choice: dump/restore, not pg_upgrade

Both work. This deployment uses **dump and restore**, for reasons that are specific to it:

- The dataset is small (one tenant-scale SaaS on one box) and the whole path — `pg_dump`,
  `psql`, encryption, object storage — is already built, tested and rehearsed every time
  `make restore-check` runs. `pg_upgrade` would be a second, unrehearsed mechanism.
- `pg_upgrade --link` needs both binary versions present in one filesystem, which in a
  containerised deployment means a purpose-built image carrying two major versions. That image
  would exist solely for upgrade days and would be exercised only on upgrade days.
- Dump/restore recreates the cluster with the new version's own `initdb` defaults. PostgreSQL 18
  turns data checksums on by default; `pg_upgrade` carries the old cluster's setting forward.

Revisit this when the dump stops fitting in the maintenance window. `pg_upgrade --link` is the
right answer at that point, and the answer changes with the data size, not with taste.

## Downtime

The window is *dump + restore + reindex*, with writes stopped for all of it. At the current data
volume the encrypted dump is kilobytes and the whole loop runs in seconds; the honest planning
number is **the time the rehearsal takes on a copy of production, measured, not estimated**.
Measure it by running step 3 against a scratch database before scheduling the window.

## Procedure

Run every step as the deploy user on the server. `$OLD` is the current major (e.g. 17), `$NEW`
the target (18).

### 1. Rehearse first, on a copy

```bash
docker compose run --rm restore-check
```

This is the whole upgrade in miniature: newest backup → decrypt → load into a throwaway
database → assert the Alembic head and core row counts. It must exit 0 and print
`db_restore_check_succeeded` **before** the window opens. If it does not, there is no upgrade
to plan; there is a backup to fix.

### 2. Stop writers, take the pre-upgrade backup

```bash
docker compose stop api celery-worker celery-beat
docker compose run --rm backup
```

Stopping the writers first is what makes the backup a consistent restore point rather than a
snapshot of a moving database. Record the `object_key` and `ciphertext_sha256` from
`db_backup_succeeded`; that object is the rollback.

### 3. Take a plain local dump as well

The off-server encrypted backup is the durable copy. A second, local, uncompressed dump makes
the restore step fast and keeps the rollback independent of object storage being reachable:

```bash
docker compose exec -T postgres \
  pg_dump --no-owner --no-privileges --format=plain \
  "postgresql://socialpilot@127.0.0.1:5432/socialpilot" > /var/tmp/pre-upgrade.sql
```

Delete this file when the upgrade is confirmed — it is plaintext, and
[ADR-013](../adr/ADR-013-single-server-deployment-topology.md) is explicit that no plaintext
dump is left on the server's own disk.

### 4. Move the old data directory aside — do not delete it

```bash
docker compose down
docker volume ls | grep postgres_data
docker run --rm -v socialpilot-ai_postgres_data:/v alpine \
  sh -c "mv /v/$OLD /v/$OLD.pre-upgrade 2>/dev/null || mv /v/data /v/data.pre-upgrade"
```

The rollback is renaming it back. Nothing here is destructive until step 7.

### 5. Point Compose at the new image and start

Change `image:` in `compose.yaml` to the new pinned tag, confirmed against the registry at that
moment — never from memory ([AGENTS.md](../../AGENTS.md) context-budget rules). Then:

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U socialpilot -d socialpilot -tAc "SELECT version()"
```

The version string must name `$NEW`. An empty cluster here is expected: it is fresh.

### 6. Restore, then verify against numbers taken before the window

```bash
docker compose exec -T postgres \
  psql -v ON_ERROR_STOP=1 "postgresql://socialpilot@127.0.0.1:5432/socialpilot" \
  < /var/tmp/pre-upgrade.sql
docker compose up -d api
docker compose exec -T api alembic current      # must print the same head as before
```

Compare row counts against the pre-upgrade values for at least
`businesses`, `media_assets`, `jobs`, `credit_ledger`, `usage_reservations`,
`entitlement_ledger_anchors` — the same set the rehearsal checks, and the ledger tables are in
it because they are the ones a partial restore breaks most quietly.

Then confirm the ledger's guards came back, not just its rows. They live in the dump's post-data
section, so a truncated restore loses them while leaving the data looking correct:

```bash
docker compose exec -T postgres psql -U socialpilot -d socialpilot -tAc \
  "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE c.relname = 'credit_ledger' AND NOT t.tgisinternal"
```

Two triggers: `trg_credit_ledger_append_only` and `trg_credit_ledger_insert_guard`. One or zero
means the restore is not finished, whatever the row counts say.

### 7. Re-plan, then start the workers

`ANALYZE` after a restore: the new cluster has no statistics, and the first queries would plan
against nothing.

```bash
docker compose exec -T postgres psql -U socialpilot -d socialpilot -c "ANALYZE"
docker compose --profile worker up -d
```

Only now delete `/var/tmp/pre-upgrade.sql` and, after a full retention cycle has proven the new
cluster, the `.pre-upgrade` directory.

### 8. Move CI in the same change

`.github/workflows/verify.yml` pins the same service images as `compose.yaml`. They are updated
together or the pipeline stops testing what production runs.

## Rollback

Before step 7: `docker compose down`, rename `$OLD.pre-upgrade` back, revert the `image:` tag,
`docker compose up -d`. Nothing was deleted.

After step 7 and after writes have resumed: the rollback is a restore from the step-2 backup,
and it loses everything written since. That is why the workers stay down until the verification
in step 6 has actually passed.

## Valkey / broker upgrades

Not comparable, and deliberately so. The broker holds no system-of-truth state
([ADR-013](../adr/ADR-013-single-server-deployment-topology.md)); jobs are durable PostgreSQL
rows and Celery messages are wake-up signals carrying no arguments
(`app/infrastructure/celery_publisher.py`). Upgrading is stop, change the tag, start. In-flight
messages may be lost, and the beat tick re-drives every drain, which is what makes that
acceptable.
