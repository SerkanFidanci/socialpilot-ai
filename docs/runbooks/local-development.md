# Local Development

## Start Docker Compose

From the repository root, build and start the local services:

```powershell
docker compose up -d --build
docker compose ps
```

By default, the API is published on `http://localhost:8000`; set `API_HOST_PORT` to use a different host port. PostgreSQL and Valkey share the internal `backend` network with the API and also join `edge` solely so Docker Desktop can publish their loopback-only development ports on `127.0.0.1:55432` and `127.0.0.1:56379`; override those ports with `POSTGRES_HOST_PORT` and `REDIS_HOST_PORT`.

The broker/cache service is **`valkey`**, not `redis` (ADR-010) — use that name with `docker compose exec`, `logs` and `ps`. The application-side variable names (`REDIS_URL`, `REDIS_PORT`, `REDIS_HOST_PORT`) and the `redis://` URL scheme are unchanged: the client library really is `redis-py` and it speaks to Valkey without knowing the difference.

Continue once `docker compose ps` reports each service as `healthy`; immediately after a forced recreate, Docker Desktop can still be attaching the internal network aliases.

> **If a service is healthy but another container cannot resolve its name**, check the network attachments before anything else. `postgres`, `valkey` and `minio` each declare `[backend, edge]`, and Docker Desktop sometimes attaches only one of them — usually to whichever container it starts first. The API hides it (it is on both networks, so it reaches the service over `edge`); the symptom surfaces on `backend`-only services, i.e. the worker and beat crash-looping with `Temporary failure in name resolution`. It has hit both `valkey` and `postgres` in this repository. Diagnose and fix:
>
> ```powershell
> docker inspect <project>-postgres-1 --format '{{json .NetworkSettings.Networks}}'
> ```
>
> ```powershell
> docker compose up -d --force-recreate postgres
> ```
>
> Recreating is safe: the data is in a named volume. Restart `celery-worker` and `celery-beat` afterwards so they stop backing off.

## Runtime images

| Service | Image |
|---|---|
| postgres | `postgres:18.4-alpine` |
| valkey | `valkey/valkey:9.1.1-alpine` |
| minio | `minio/minio:RELEASE.2025-04-22T22-12-26Z` |
| api / celery-worker / celery-beat | built from `services/api/Dockerfile`, **`runtime`** target |
| backup / restore-check | built from `services/api/Dockerfile`, **`backup`** target |

The Dockerfile has two stages and Docker builds the *last* one when no target is named, so every
Compose service that uses it pins `target:` explicitly. Never drop those lines: without them the
API, worker and beat images silently pick up the backup runner's database client, which ADR-013
forbids.

PostgreSQL 18 moved `PGDATA` to `/var/lib/postgresql/18/docker` and the volume mount follows it.
A stale volume from an older checkout mounted at the 16-era `/var/lib/postgresql/data` does not
error — it gives an empty database. If a checkout that used to work comes up with no data,
recreate the volume:

```powershell
docker compose down -v
docker compose up -d --build
docker compose exec -T api alembic upgrade head
```

## Check health endpoints

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Use the configured `API_HOST_PORT` if it differs from `8000`.

## Enable the real object-storage byte path

Compose defaults `STORAGE_ADAPTER` to `fake` so the opt-in control-plane integration suite keeps
its in-process, byte-free adapter. The mobile demo's real multipart upload needs the
S3-compatible MinIO adapter instead. Enable it per checkout **without** changing the Compose
default: create a `.env` in the repository root containing

```dotenv
STORAGE_ADAPTER=s3
```

then recreate the API so it reads the value:

```powershell
docker compose up -d api
```

MinIO and its one-shot bucket provisioning already start in the default profile, so no other
service needs enabling. Do not flip the Compose default to `s3`: the control-plane tests depend
on `fake`.

## Install backend dependencies

The backend uses [`uv`](https://docs.astral.sh/uv/) with a committed lockfile
(`services/api/uv.lock`). Install the exact locked set once, including the dev tools:

```powershell
cd services/api
uv sync --locked --all-extras
```

This creates `services/api/.venv`. `uv sync --locked` fails if the lockfile and `pyproject.toml`
disagree, so CI and local installs resolve the identical dependency closure. Prefix the commands
below with `uv run`, or activate `.venv` for the shell.

## Run tests

Run the backend suite from `services/api`:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

With local Compose services running, execute the container suite (including the opt-in PostgreSQL and Redis integration tests):

```powershell
docker compose stop celery-worker celery-beat
```

```powershell
docker compose exec -T -e RUN_INTEGRATION_TESTS=1 api pytest
```

Stop `celery-worker` and `celery-beat` first. Integration tests share the development database, and beat-scheduled drains run every few seconds, so a live worker would claim the fixtures' jobs and dispatch their outbox events before the assertions run. A dedicated test database is a Phase 1E concern.

## Run workers and beat

Both live in the `worker` Compose profile, so plain `docker compose up -d` leaves them out:

```powershell
docker compose --profile worker up -d
```

Beat is a separate read-only service, never a worker `-B` flag, and development assumes exactly one beat replica. Confirm the worker registered every task and that beat is ticking:

```powershell
docker compose exec -T celery-worker celery -A app.infrastructure.celery_app inspect registered
```

```powershell
docker compose logs --tail=40 celery-beat
```

Worker and beat containers bake their source into the image, so rebuild them after changing `services/api`:

```powershell
docker compose --profile worker up -d --build celery-worker celery-beat
```

## Run migrations

```powershell
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic downgrade base
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic current
docker compose exec -T api alembic heads
```

## Take a backup and rehearse a restore

Both live in the `backup` Compose profile and both are one-shot: they run, print a structured
result and exit. MinIO stands in for the production R2 bucket, so no extra configuration is
needed locally — `BACKUP_ENCRYPTION_KEY` falls back to a development passphrase.

```powershell
docker compose run --rm backup
```

```powershell
docker compose run --rm restore-check
```

The first prints `db_backup_succeeded` with the object key, ciphertext size and sha256. The
second takes a backup and then restores that backup into a throwaway `socialpilot_restore_check`
database, printing `db_restore_check_succeeded` with the restored Alembic head and core row
counts; add `--no-deps` to skip taking a new backup and rehearse the newest stored object
instead. Details and the production schedule are in
[operations.md](operations.md).

## Generate and verify OpenAPI

Generate the deterministic public contract from a Python environment with the backend dependencies installed:

```powershell
python services/api/scripts/generate_openapi.py
```

On Linux/macOS or in CI, `make generate-docs` regenerates `docs/generated/openapi.json`; `make check-openapi` regenerates it and fails when the committed contract is stale.

## Windows Docker Desktop port troubleshooting

Confirm the resolved mapping and the actual published API port:

```powershell
docker compose config
docker compose port api 8000
docker compose ps
```

The API must join both the `edge` network (host publishing) and the internal `backend` network (PostgreSQL and Redis). PostgreSQL and Redis also join `edge` solely for loopback host-port publication; application traffic between services always uses `backend`. If no API port is shown after a configuration change, recreate the services:

```powershell
docker compose up -d --build --force-recreate
```

If port `8000` is already in use, select a free port for this shell and use it in the health commands:

```powershell
$env:API_HOST_PORT = "8001"
docker compose up -d --force-recreate
Invoke-RestMethod http://localhost:8001/health/live
```

## Build the Android APK (JDK + Android command-line tools)

`flutter test` compiles the Dart sources but building an installable `--debug` APK needs a JDK
and the Android SDK, which a fresh Windows machine does not have. `apps/mobile/android` targets
**Java 17** (`app/build.gradle.kts`), and the Android platform/build-tools are downloaded by
Gradle on first build once the SDK licenses are accepted. These commands run from **PowerShell**.

### 1. Install a JDK 17

```powershell
choco install temurin17 -y
```

Then set `JAVA_HOME` for the current session (adjust the path to the installed JDK) and confirm:

```powershell
$env:JAVA_HOME = (Get-ChildItem "C:\Program Files\Eclipse Adoptium\jdk-17*" | Select-Object -First 1).FullName
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
java -version
```

Persist `JAVA_HOME` across shells once it is confirmed:

```powershell
[Environment]::SetEnvironmentVariable("JAVA_HOME", $env:JAVA_HOME, "User")
```

### 2. Install the Android command-line tools

Download **"Command line tools only"** from
<https://developer.android.com/studio#command-line-tools-only> (the archive's build number
changes over time — take the current one). Unzip so the tools live at
`%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\bin`:

```powershell
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
New-Item -ItemType Directory -Force "$sdk\cmdline-tools" | Out-Null
Expand-Archive -Path "$env:USERPROFILE\Downloads\commandlinetools-win-*_latest.zip" -DestinationPath "$sdk\cmdline-tools"
Rename-Item "$sdk\cmdline-tools\cmdline-tools" "latest"
[Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", $sdk, "User")
$env:ANDROID_SDK_ROOT = $sdk
$env:Path = "$sdk\cmdline-tools\latest\bin;$sdk\platform-tools;$env:Path"
```

Install `platform-tools` and accept every SDK license (Gradle downloads the matching platform
and build-tools automatically on the first build):

```powershell
sdkmanager "platform-tools"
sdkmanager --licenses
```

### 3. Point Flutter at the toolchain and confirm

```powershell
flutter config --jdk-dir "$env:JAVA_HOME"
flutter config --android-sdk "$env:ANDROID_SDK_ROOT"
flutter doctor
flutter doctor --android-licenses
```

`flutter doctor` must show green for "Android toolchain"; resolve anything it still flags before
building.

### 4. Build

```powershell
cd apps/mobile
flutter build apk --debug
```

The debug APK is written to `apps/mobile/build/app/outputs/flutter-apk/app-debug.apk`.

> **Status (W02, 2026-07-30):** these steps are documented but not yet executed end to end on a
> clean machine — this environment has neither a JDK (`JAVA_HOME` is unset, `java` is absent) nor
> a resolvable Flutter SDK, which is blocker **B2** itself. The first mobile session with the
> toolchain installed should run through them and record the outcome here.
