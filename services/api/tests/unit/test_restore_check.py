"""Restore-rehearsal pure logic: latest-backup selection and Alembic head verification.

The download -> decrypt -> psql load -> query loop runs against real PostgreSQL in Docker; these
tests pin the selection and head-comparison logic that decides pass/fail.
"""

from __future__ import annotations

import pytest

from scripts.backup_db import BackupError
from scripts.restore_check import (
    RestoreConfig,
    parse_alembic_heads,
    select_latest_backup,
    verify_head,
)


def _key(stamp: str) -> str:
    return f"backups/2026/x/socialpilot-{stamp}.sql.gz.enc"


def test_select_latest_backup_picks_newest_timestamp() -> None:
    keys = [_key("20260728T000000Z"), _key("20260730T090000Z"), _key("20260730T010000Z")]
    assert select_latest_backup(keys) == _key("20260730T090000Z")


def test_select_latest_backup_ignores_unparseable_and_raises_when_empty() -> None:
    with pytest.raises(BackupError) as excinfo:
        select_latest_backup(["backups/not-a-backup.txt"])
    assert excinfo.value.error_code == "RESTORE_NO_BACKUP_FOUND"


def test_parse_alembic_heads_reads_revision_ids() -> None:
    text = "0009_video_understanding (head)\n"
    assert parse_alembic_heads(text) == {"0009_video_understanding"}


def test_verify_head_passes_on_match() -> None:
    verify_head({"0009_video_understanding"}, {"0009_video_understanding"})


def test_verify_head_rejects_mismatch() -> None:
    with pytest.raises(BackupError) as excinfo:
        verify_head({"0008_old"}, {"0009_video_understanding"})
    assert excinfo.value.error_code == "RESTORE_HEAD_MISMATCH"


def test_verify_head_rejects_missing_alembic_version() -> None:
    with pytest.raises(BackupError) as excinfo:
        verify_head(set(), {"0009_video_understanding"})
    assert excinfo.value.error_code == "RESTORE_NO_ALEMBIC_VERSION"


def test_restore_config_requires_scratch_database() -> None:
    with pytest.raises(BackupError) as excinfo:
        RestoreConfig.from_env({"BACKUP_ENCRYPTION_KEY": "0123456789abcdef"})
    assert excinfo.value.error_code == "RESTORE_SCRATCH_DB_MISSING"


def test_restore_config_reads_optional_explicit_key() -> None:
    config = RestoreConfig.from_env(
        {
            "RESTORE_CHECK_DATABASE_URL": "postgresql+asyncpg://u:p@h/scratch",
            "BACKUP_ENCRYPTION_KEY": "0123456789abcdef",
            "RESTORE_CHECK_BACKUP_KEY": "backups/2026/x/socialpilot-20260730T010000Z.sql.gz.enc",
        }
    )
    assert config.explicit_backup_key is not None
    assert config.scratch_database_url.endswith("/scratch")
