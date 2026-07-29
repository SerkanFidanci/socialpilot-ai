# Local Development

## Start Docker Compose

From the repository root, build and start the local services:

```powershell
docker compose up -d --build
docker compose ps
```

By default, the API is published on `http://localhost:8000`; set `API_HOST_PORT` to use a different host port. PostgreSQL and Redis share the internal `backend` network with the API and also join `edge` solely so Docker Desktop can publish their loopback-only development ports on `127.0.0.1:55432` and `127.0.0.1:56379`; override those ports with `POSTGRES_HOST_PORT` and `REDIS_HOST_PORT`.

Continue once `docker compose ps` reports each service as `healthy`; immediately after a forced recreate, Docker Desktop can still be attaching the internal network aliases.

## Check health endpoints

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Use the configured `API_HOST_PORT` if it differs from `8000`.

## Run tests

Run the backend suite from `services/api` with the project Python environment:

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy .
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
