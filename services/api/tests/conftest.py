"""Shared test configuration for infrastructure-free API tests."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

# Overridden, not defaulted: a developer running the stack with STORAGE_ADAPTER=s3 must still
# be able to run this suite. Tests that exercise a real provider set the adapter explicitly on
# their own Settings object; everything else needs the byte-free fake and its test hooks.
os.environ["STORAGE_ADAPTER"] = "fake"
