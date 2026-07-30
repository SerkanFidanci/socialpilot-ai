"""Backup pure logic: URL mapping, secret scan, retention, SigV4, key parsing, encryption.

The full pg_dump -> gzip -> openssl -> S3 loop is exercised in Docker (real PostgreSQL + MinIO);
these tests pin the pure, host-runnable pieces plus a real openssl round-trip when openssl exists.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.backup_db import (
    BackupConfig,
    BackupError,
    S3Target,
    assert_no_plaintext_secrets,
    backup_object_key,
    decrypt_file,
    encrypt_file,
    libpq_dsn,
    parse_list_objects_v2,
    select_expired_keys,
    signed_headers,
)


def test_libpq_dsn_strips_asyncpg_driver() -> None:
    assert libpq_dsn("postgresql+asyncpg://u:p@host:5432/db") == "postgresql://u:p@host:5432/db"
    assert libpq_dsn("postgresql://u:p@host/db") == "postgresql://u:p@host/db"


def test_libpq_dsn_rejects_non_postgres() -> None:
    with pytest.raises(BackupError) as excinfo:
        libpq_dsn("mysql://x")
    assert excinfo.value.error_code == "BACKUP_BAD_DATABASE_URL"


def test_backup_object_key_is_dated_and_sortable() -> None:
    key = backup_object_key("backups/", datetime(2026, 7, 30, 1, 22, 33, tzinfo=UTC))
    assert key == "backups/2026/2026-07-30/socialpilot-20260730T012233Z.sql.gz.enc"


def test_assert_no_plaintext_secrets_passes_on_encrypted_dump() -> None:
    # Ciphertext-looking token value must not trip the scanner.
    assert_no_plaintext_secrets(
        "COPY oauth_credentials (token) FROM stdin;\nenc:v1:9f83aa2c4471deadbeef\n"
    )


@pytest.mark.parametrize("token", ["ya29.aBc", "1//0xdef", "EAABxyz", "Bearer abc123"])
def test_assert_no_plaintext_secrets_catches_plaintext_tokens(token: str) -> None:
    with pytest.raises(BackupError) as excinfo:
        assert_no_plaintext_secrets(f"COPY oauth_credentials (token) FROM stdin;\n{token}\n")
    assert excinfo.value.error_code == "BACKUP_PLAINTEXT_SECRET_DETECTED"


def _key(stamp: str) -> str:
    return f"backups/2026/x/socialpilot-{stamp}.sql.gz.enc"


def test_select_expired_keeps_daily_window_and_one_per_week() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    keys = [
        _key("20260730T000000Z"),  # today — inside 7-day daily window, kept
        _key("20260720T000000Z"),  # Mon — outside daily window, first of its ISO week, kept
        _key("20260722T000000Z"),  # Wed, same ISO week as 07-20 — expired (week already kept)
        _key("20260610T000000Z"),  # ~7 weeks ago — weekly kept
        _key("20260101T000000Z"),  # far past — beyond weekly window, expired
        "backups/unparseable-object.txt",  # never proposed for deletion
    ]
    expired = select_expired_keys(keys, now, daily_days=7, weekly_weeks=8)

    assert _key("20260730T000000Z") not in expired
    assert _key("20260720T000000Z") not in expired  # first of its week, kept
    assert _key("20260722T000000Z") in expired  # ISO week already covered by 07-20
    assert _key("20260101T000000Z") in expired  # beyond weekly window
    assert "backups/unparseable-object.txt" not in expired


def test_parse_list_objects_v2_extracts_keys() -> None:
    xml = (
        b'<?xml version="1.0"?>'
        b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<Contents><Key>backups/a.enc</Key></Contents>"
        b"<Contents><Key>backups/b.enc</Key></Contents>"
        b"</ListBucketResult>"
    )
    assert parse_list_objects_v2(xml) == ["backups/a.enc", "backups/b.enc"]


def _target() -> S3Target:
    return S3Target(
        endpoint="http://minio:9000",
        region="us-east-1",
        bucket="socialpilot-media",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secret",
        path_style=True,
    )


def test_signed_headers_are_deterministic_and_bind_the_request() -> None:
    headers = signed_headers(
        _target(),
        method="PUT",
        object_key="backups/x.enc",
        query={},
        payload_sha256="UNSIGNED-PAYLOAD",
        amz_date="20260730T012233Z",
    )
    assert headers["Host"] == "minio:9000"
    assert headers["x-amz-content-sha256"] == "UNSIGNED-PAYLOAD"
    assert headers["x-amz-date"] == "20260730T012233Z"
    assert headers["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260730/us-east-1/s3/aws4_request"
    )
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in headers["Authorization"]
    # A different date must produce a different signature (the signature is date-scoped).
    other = signed_headers(
        _target(),
        method="PUT",
        object_key="backups/x.enc",
        query={},
        payload_sha256="UNSIGNED-PAYLOAD",
        amz_date="20260731T012233Z",
    )
    assert other["Authorization"] != headers["Authorization"]


def test_backup_config_requires_a_real_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BackupError) as excinfo:
        BackupConfig.from_env({"BACKUP_ENCRYPTION_KEY": "short"})
    assert excinfo.value.error_code == "BACKUP_ENCRYPTION_KEY_MISSING"


def test_backup_config_normalizes_prefix() -> None:
    config = BackupConfig.from_env(
        {"BACKUP_ENCRYPTION_KEY": "0123456789abcdef", "BACKUP_S3_PREFIX": "dumps"}
    )
    assert config.s3_prefix == "dumps/"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed on host")
def test_encrypt_decrypt_round_trip(tmp_path: Path) -> None:
    plaintext = tmp_path / "plain.bin"
    plaintext.write_bytes(b"pg_dump payload with secrets" * 100)
    encrypted = tmp_path / "cipher.enc"
    recovered = tmp_path / "recovered.bin"

    encrypt_file(plaintext, encrypted, "correct horse battery staple", "openssl")
    assert encrypted.read_bytes() != plaintext.read_bytes()  # actually encrypted
    decrypt_file(encrypted, recovered, "correct horse battery staple", "openssl")

    assert recovered.read_bytes() == plaintext.read_bytes()


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed on host")
def test_decrypt_fails_with_wrong_passphrase(tmp_path: Path) -> None:
    plaintext = tmp_path / "plain.bin"
    plaintext.write_bytes(b"payload")
    encrypted = tmp_path / "cipher.enc"
    encrypt_file(plaintext, encrypted, "right-passphrase-1234", "openssl")

    with pytest.raises((BackupError, subprocess.SubprocessError)):
        decrypt_file(encrypted, tmp_path / "out.bin", "wrong-passphrase-9999", "openssl")
