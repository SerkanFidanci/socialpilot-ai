"""Opt-in integration verification for the empty Alembic bootstrap revision."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="requires a dedicated PostgreSQL test service",
)
def test_alembic_upgrade_and_downgrade() -> None:
    environment = os.environ.copy()
    api_directory = Path(__file__).resolve().parents[2]

    for command in (
        ["upgrade", "head"],
        ["downgrade", "base"],
        ["upgrade", "head"],
    ):
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            cwd=api_directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
