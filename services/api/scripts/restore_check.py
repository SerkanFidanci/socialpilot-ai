"""Restore rehearsal: an untested backup is not a backup (ADR-013).

Downloads the most recent encrypted backup, decrypts and decompresses it, loads it into an
**empty scratch database**, and then proves the restore is real:

1. the restored ``alembic_version`` matches the code's Alembic head, and
2. a set of core tables exists and is queryable (row-count sanity).

Run against a throwaway database only — it loads a full dump. The scratch database URL is a
separate variable (``RESTORE_CHECK_DATABASE_URL``) so this can never point at production by
default. One command: ``make restore-check``.

Prerequisites on the runner: ``psql`` (postgresql-client) and ``openssl`` — same as the backup.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from scripts.backup_db import (
    BackupError,
    BackupObjectStore,
    S3Target,
    _parse_key_timestamp,
    decrypt_file,
    libpq_dsn,
)

# Core tables that must survive a restore. Not exhaustive — a representative slice spanning
# identity, media, and the durable job machinery, enough to prove the schema and data loaded.
_ROW_COUNT_TABLES = ("businesses", "media_assets", "jobs")

log = structlog.get_logger("restore_check")


@dataclass(frozen=True)
class RestoreConfig:
    scratch_database_url: str
    encryption_key: str
    s3_prefix: str
    explicit_backup_key: str | None
    psql_binary: str
    openssl_binary: str
    s3_timeout_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RestoreConfig:
        source = os.environ if env is None else env
        scratch = source.get("RESTORE_CHECK_DATABASE_URL", "")
        if not scratch:
            raise BackupError(
                "RESTORE_SCRATCH_DB_MISSING",
                "RESTORE_CHECK_DATABASE_URL must point at an empty scratch database",
            )
        key = source.get("BACKUP_ENCRYPTION_KEY", "")
        if len(key) < 16:
            raise BackupError("BACKUP_ENCRYPTION_KEY_MISSING", "BACKUP_ENCRYPTION_KEY must be set")
        prefix = source.get("BACKUP_S3_PREFIX", "backups/")
        if not prefix.endswith("/"):
            prefix += "/"
        return cls(
            scratch_database_url=scratch,
            encryption_key=key,
            s3_prefix=prefix,
            explicit_backup_key=source.get("RESTORE_CHECK_BACKUP_KEY") or None,
            psql_binary=source.get("BACKUP_PSQL_BINARY", "psql"),
            openssl_binary=source.get("BACKUP_OPENSSL_BINARY", "openssl"),
            s3_timeout_seconds=float(source.get("BACKUP_S3_TIMEOUT_SECONDS", "300")),
        )


# --------------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------------


def select_latest_backup(keys: Iterable[str]) -> str:
    """Return the newest backup key by its embedded timestamp."""

    dated = [(moment, key) for key in keys if (moment := _parse_key_timestamp(key)) is not None]
    if not dated:
        raise BackupError("RESTORE_NO_BACKUP_FOUND", "no dated backup objects under the prefix")
    return max(dated)[1]


def parse_alembic_heads(text: str) -> set[str]:
    """Parse ``alembic heads`` output into the set of head revision ids.

    A line looks like ``0009_video_understanding (head)``; the revision is the first token.
    """

    heads: set[str] = set()
    for line in text.splitlines():
        token = line.strip().split()
        if token:
            heads.add(token[0])
    return heads


def verify_head(restored: set[str], code_heads: set[str]) -> None:
    """Fail unless the restored ``alembic_version`` matches the code's head exactly."""

    if not restored:
        raise BackupError("RESTORE_NO_ALEMBIC_VERSION", "restored database has no alembic_version")
    if restored != code_heads:
        raise BackupError(
            "RESTORE_HEAD_MISMATCH",
            f"restored head {sorted(restored)} != code head {sorted(code_heads)}",
        )


# --------------------------------------------------------------------------------------------
# Side-effecting steps (verified in Docker)
# --------------------------------------------------------------------------------------------


def _psql_query(dsn: str, sql: str, psql_binary: str) -> str:
    completed = subprocess.run(
        [psql_binary, dsn, "-tAqc", sql],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BackupError(
            "RESTORE_QUERY_FAILED",
            f"psql query failed: {completed.stderr.decode(errors='replace')[:200]}",
        )
    return completed.stdout.decode().strip()


def _psql_load(dsn: str, dump: Path, psql_binary: str) -> None:
    completed = subprocess.run(
        [psql_binary, dsn, "-v", "ON_ERROR_STOP=1", "-f", str(dump)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise BackupError(
            "RESTORE_LOAD_FAILED",
            f"psql restore failed: {completed.stderr.decode(errors='replace')[:500]}",
        )


def _code_alembic_heads() -> set[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )
    if completed.returncode != 0:
        raise BackupError(
            "RESTORE_ALEMBIC_HEADS_FAILED",
            f"alembic heads failed: {completed.stderr.decode(errors='replace')[:200]}",
        )
    return parse_alembic_heads(completed.stdout.decode())


def _gunzip(source: Path, destination: Path) -> None:
    import gzip

    with gzip.open(source, "rb") as gz, destination.open("wb") as sink:
        for chunk in iter(lambda: gz.read(1 << 20), b""):
            sink.write(chunk)


def run_restore_check(settings: Settings, config: RestoreConfig) -> dict[str, object]:
    scratch_dsn = libpq_dsn(config.scratch_database_url)
    target = S3Target.from_settings(settings)
    store = BackupObjectStore(target, timeout_seconds=config.s3_timeout_seconds)

    object_key = config.explicit_backup_key or select_latest_backup(
        store.list_keys(config.s3_prefix)
    )

    with tempfile.TemporaryDirectory(prefix="sp-restore-") as tmp:
        workdir = Path(tmp)
        enc_path = workdir / "backup.sql.gz.enc"
        gz_path = workdir / "backup.sql.gz"
        sql_path = workdir / "backup.sql"

        store.get_to_file(object_key, enc_path)
        decrypt_file(enc_path, gz_path, config.encryption_key, config.openssl_binary)
        _gunzip(gz_path, sql_path)
        _psql_load(scratch_dsn, sql_path, config.psql_binary)

    restored_head = {
        row
        for row in _psql_query(
            scratch_dsn, "SELECT version_num FROM alembic_version", config.psql_binary
        ).splitlines()
        if row
    }
    verify_head(restored_head, _code_alembic_heads())

    row_counts: dict[str, int] = {}
    for table in _ROW_COUNT_TABLES:
        raw = _psql_query(scratch_dsn, f"SELECT count(*) FROM {table}", config.psql_binary)
        row_counts[table] = int(raw)

    result: dict[str, object] = {
        "object_key": object_key,
        "restored_head": sorted(restored_head),
        "row_counts": row_counts,
    }
    log.info("db_restore_check_succeeded", **result)
    return result


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        config = RestoreConfig.from_env()
        run_restore_check(settings, config)
    except BackupError as exc:
        log.error("db_restore_check_failed", error_code=exc.error_code, error=str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - a failed rehearsal must never exit 0
        log.error("db_restore_check_failed", error_code="RESTORE_UNEXPECTED_ERROR", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
