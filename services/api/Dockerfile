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
