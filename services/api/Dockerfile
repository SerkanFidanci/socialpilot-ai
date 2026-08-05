FROM python:3.13-slim AS runtime

ARG INSTALL_DEV=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Deterministic installs from uv.lock. The environment lives OUTSIDE the source tree
    # (/opt/venv, not services/api/.venv) so the Compose read-only source bind mount cannot
    # shadow the installed packages.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

# Pinned uv binary; the 0.11.31 tag was verified against the astral-sh/uv registry at write time.
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app && adduser --system --ingroup app app

WORKDIR /app/services/api

# Layer 1: resolve dependencies from the lockfile without the project itself, so this layer
# is cached across source-only changes.
COPY services/api/pyproject.toml services/api/uv.lock ./
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      uv sync --locked --no-install-project --all-extras; \
    else \
      uv sync --locked --no-install-project; \
    fi

# Layer 2: bring in the source and install the project into the environment.
COPY services/api /app/services/api
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      uv sync --locked --all-extras; \
    else \
      uv sync --locked; \
    fi

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Backup runner image (W06). ADR-013 keeps the stateless API image free of database clients, and
# that still holds: this is a *later stage*, so the `runtime` image Compose ships as `api`,
# `celery-worker` and `celery-beat` is unchanged. Only the `backup`/`restore-check` services
# build this target.
#
# The client comes from PostgreSQL's own apt repository rather than Debian's, because trixie
# ships postgresql-client 17 and pg_dump refuses a server newer than itself — a 17 client
# against the 18.4 server would abort with a version mismatch and the backup would never run.
# openssl is already present in python:3.13-slim; it is named explicitly so a base-image change
# that drops it fails here rather than at 02:17 in a cron job.
FROM runtime AS backup

USER root

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl gnupg openssl \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         -o /etc/apt/keyrings/pgdg.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client-18 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && pg_dump --version && psql --version && openssl version

USER app

# No server port and no long-lived process: both entry points are one-shot scripts whose exit
# status is the result. The default is the backup; `restore-check` overrides it.
CMD ["python", "-m", "scripts.backup_db"]
