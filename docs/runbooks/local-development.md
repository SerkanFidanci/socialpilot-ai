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

With local Compose services running, execute the container suite (including the opt-in Alembic integration test):

```powershell
docker compose exec -T -e RUN_INTEGRATION_TESTS=1 api pytest
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
