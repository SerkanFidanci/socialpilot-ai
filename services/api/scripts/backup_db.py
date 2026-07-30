"""Off-server, encrypted PostgreSQL backup for the single-server deployment (ADR-013).

One server is one point of failure and the production database will not live in git, so a
daily dump must leave the box. This script:

1. runs ``pg_dump`` (plain SQL) so the artifact is inspectable and restorable with ``psql``;
2. asserts the dump carries no plaintext OAuth token — credentials are envelope-encrypted at
   rest, so a plaintext token in the dump would mean an upstream regression, not a backup bug;
3. gzips (stdlib) then encrypts with ``openssl`` AES-256-CBC + PBKDF2 — the backup at rest is
   ciphertext;
4. uploads the ciphertext to object storage (R2/S3) under a dated key, keeping **no copy on the
   server's own disk**;
5. prunes old backups by a documented daily/weekly retention policy;
6. emits a structured success log, or on any failure a structured ``db_backup_failed`` error log
   with an error code, so a failed backup is never silent.

Metrics are deferred to W05 (OpenTelemetry); this script produces logs only.

Runtime prerequisites on the backup host (the single server or a DB-adjacent runner):
``pg_dump`` (postgresql-client) and ``openssl``. The stateless API image intentionally ships
neither; see docs/runbooks/operations.md.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import httpx
import structlog

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

_ALGORITHM = "AWS4-HMAC-SHA256"
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# Known plaintext OAuth access/refresh token shapes. oauth_credentials stores these
# envelope-encrypted, so any of these appearing in a dump is a real leak to catch, not a match on
# ciphertext. The list is intentionally conservative — a false positive fails the backup loudly,
# which is the safe direction.
_PLAINTEXT_TOKEN_MARKERS = (
    "ya29.",  # Google OAuth access token
    "1//",  # Google OAuth refresh token
    "EAAB",  # Facebook/Instagram Graph long-lived token
    "IGQVJ",  # Instagram Basic Display token
    "Bearer ",  # any literal bearer header captured into a column
)

log = structlog.get_logger("backup_db")


class BackupError(RuntimeError):
    """A backup step failed; carries a stable error code for the failure log."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code


@dataclass(frozen=True)
class BackupConfig:
    """Backup knobs resolved from the environment, separate from app Settings."""

    s3_prefix: str
    encryption_key: str
    pg_dump_binary: str
    openssl_binary: str
    retention_daily_days: int
    retention_weekly_weeks: int
    s3_timeout_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BackupConfig:
        source = os.environ if env is None else env
        key = source.get("BACKUP_ENCRYPTION_KEY", "")
        if len(key) < 16:
            raise BackupError(
                "BACKUP_ENCRYPTION_KEY_MISSING",
                "BACKUP_ENCRYPTION_KEY must be set to at least 16 characters",
            )
        prefix = source.get("BACKUP_S3_PREFIX", "backups/")
        if not prefix.endswith("/"):
            prefix += "/"
        return cls(
            s3_prefix=prefix,
            encryption_key=key,
            pg_dump_binary=source.get("BACKUP_PG_DUMP_BINARY", "pg_dump"),
            openssl_binary=source.get("BACKUP_OPENSSL_BINARY", "openssl"),
            retention_daily_days=int(source.get("BACKUP_RETENTION_DAILY_DAYS", "14")),
            retention_weekly_weeks=int(source.get("BACKUP_RETENTION_WEEKLY_WEEKS", "8")),
            s3_timeout_seconds=float(source.get("BACKUP_S3_TIMEOUT_SECONDS", "300")),
        )


# --------------------------------------------------------------------------------------------
# Pure helpers (unit-tested without PostgreSQL, openssl, or object storage)
# --------------------------------------------------------------------------------------------


def libpq_dsn(database_url: str) -> str:
    """Turn the app's ``postgresql+asyncpg://`` URL into a libpq DSN ``pg_dump`` accepts."""

    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url[len("postgresql+asyncpg://") :]
    if database_url.startswith("postgresql://"):
        return database_url
    raise BackupError("BACKUP_BAD_DATABASE_URL", "DATABASE_URL is not a PostgreSQL URL")


def backup_object_key(prefix: str, moment: datetime) -> str:
    """Dated, sortable key: ``backups/2026/2026-07-30/socialpilot-20260730T012233Z.sql.gz.enc``."""

    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}{moment:%Y}/{moment:%Y-%m-%d}/socialpilot-{stamp}.sql.gz.enc"


def assert_no_plaintext_secrets(sql_text: str) -> None:
    """Fail the backup if the dump contains a recognizable plaintext OAuth token.

    oauth_credentials stores tokens envelope-encrypted; a plaintext token here means an upstream
    write bypassed encryption. Catching it at backup time keeps the leak from being shipped
    off-box in cleartext.
    """

    for marker in _PLAINTEXT_TOKEN_MARKERS:
        index = sql_text.find(marker)
        if index != -1:
            raise BackupError(
                "BACKUP_PLAINTEXT_SECRET_DETECTED",
                f"dump contains a plaintext token marker {marker!r}",
            )


def select_expired_keys(
    keys: Iterable[str], now: datetime, daily_days: int, weekly_weeks: int
) -> list[str]:
    """Return backup keys to delete under a daily+weekly retention policy.

    Keep every backup from the last ``daily_days`` days, plus one backup per ISO week for the
    last ``weekly_weeks`` weeks (the earliest kept per week). Everything else is expired. Keys
    that do not parse to a timestamp are left untouched — this function never proposes deleting
    something it does not understand.
    """

    dated: list[tuple[datetime, str]] = []
    for key in keys:
        moment = _parse_key_timestamp(key)
        if moment is not None:
            dated.append((moment, key))
    dated.sort()

    daily_cutoff = now - timedelta(days=daily_days)
    weekly_cutoff = now - timedelta(weeks=weekly_weeks)
    kept_weeks: set[tuple[int, int]] = set()
    expired: list[str] = []
    for moment, key in dated:
        if moment >= daily_cutoff:
            continue  # inside the daily window — always kept
        if moment >= weekly_cutoff:
            week = moment.isocalendar()[:2]
            if week not in kept_weeks:
                kept_weeks.add(week)
                continue  # first backup seen for this week — kept as the weekly
        expired.append(key)
    return expired


def _parse_key_timestamp(key: str) -> datetime | None:
    marker = "socialpilot-"
    start = key.rfind(marker)
    if start == -1:
        return None
    stamp = key[start + len(marker) : start + len(marker) + 16]
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------
# Minimal SigV4 object-storage client (PUT / GET / LIST / DELETE)
# --------------------------------------------------------------------------------------------


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    initial = _sign(f"AWS4{secret}".encode(), date_stamp)
    return _sign(_sign(_sign(initial, region), "s3"), "aws4_request")


def _canonical_query(params: Mapping[str, str]) -> str:
    return "&".join(
        f"{quote(key, safe='-._~')}={quote(value, safe='-._~')}"
        for key, value in sorted(params.items())
    )


@dataclass(frozen=True)
class S3Target:
    endpoint: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    path_style: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> S3Target:
        endpoint = settings.s3_endpoint_url
        bucket = settings.s3_bucket
        access = settings.s3_access_key_id.get_secret_value()
        secret = settings.s3_secret_access_key.get_secret_value()
        missing = [
            name
            for name, value in (
                ("S3_ENDPOINT_URL", endpoint),
                ("S3_BUCKET", bucket),
                ("S3_ACCESS_KEY_ID", access),
                ("S3_SECRET_ACCESS_KEY", secret),
            )
            if not value
        ]
        if missing:
            raise BackupError(
                "BACKUP_S3_CONFIG_MISSING", f"backup upload requires {', '.join(missing)}"
            )
        return cls(
            endpoint=endpoint,
            region=settings.s3_region,
            bucket=bucket,
            access_key_id=access,
            secret_access_key=secret,
            path_style=settings.s3_force_path_style,
        )

    def _host(self) -> str:
        netloc = urlsplit(self.endpoint).netloc
        return netloc if self.path_style else f"{self.bucket}.{netloc}"

    def _path(self, object_key: str) -> str:
        encoded = quote(object_key, safe="/-._~")
        return f"/{self.bucket}/{encoded}" if self.path_style else f"/{encoded}"

    def url(self, object_key: str = "", query: Mapping[str, str] | None = None) -> str:
        scheme = urlsplit(self.endpoint).scheme
        suffix = f"?{_canonical_query(query)}" if query else ""
        return f"{scheme}://{self._host()}{self._path(object_key)}{suffix}"


def signed_headers(
    target: S3Target,
    *,
    method: str,
    object_key: str,
    query: Mapping[str, str],
    payload_sha256: str,
    amz_date: str,
) -> dict[str, str]:
    """SigV4 header-auth for one request. Returns the headers to send (pure, testable)."""

    date_stamp = amz_date[:8]
    canonical_uri = target._path(object_key)
    canonical_headers = (
        f"host:{target._host()}\nx-amz-content-sha256:{payload_sha256}\nx-amz-date:{amz_date}\n"
    )
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        (method, canonical_uri, _canonical_query(query), canonical_headers, signed, payload_sha256)
    )
    scope = f"{date_stamp}/{target.region}/s3/aws4_request"
    string_to_sign = "\n".join(
        (
            _ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        )
    )
    signature = hmac.new(
        _signing_key(target.secret_access_key, date_stamp, target.region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        f"{_ALGORITHM} Credential={target.access_key_id}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    return {
        "Host": target._host(),
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": amz_date,
        "Authorization": authorization,
    }


def parse_list_objects_v2(payload: bytes) -> list[str]:
    """Extract object keys from a ListObjectsV2 XML response."""

    root = ElementTree.fromstring(payload)
    keys: list[str] = []
    for contents in root.iter():
        if contents.tag.rsplit("}", 1)[-1] == "Key" and contents.text:
            keys.append(contents.text)
    return keys


class BackupObjectStore:
    """Thin SigV4 wrapper over the four verbs a backup needs. Every call has a timeout."""

    def __init__(self, target: S3Target, *, timeout_seconds: float) -> None:
        self._target = target
        self._timeout = timeout_seconds

    def _now(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    def put_file(self, object_key: str, source: Path) -> None:
        amz_date = self._now()
        headers = signed_headers(
            self._target,
            method="PUT",
            object_key=object_key,
            query={},
            payload_sha256=_UNSIGNED_PAYLOAD,
            amz_date=amz_date,
        )
        with source.open("rb") as body, httpx.Client(timeout=self._timeout) as client:
            response = client.put(self._target.url(object_key), content=body, headers=headers)
        self._raise_for_status(response, "BACKUP_UPLOAD_FAILED", object_key)

    def get_to_file(self, object_key: str, destination: Path) -> None:
        amz_date = self._now()
        headers = signed_headers(
            self._target,
            method="GET",
            object_key=object_key,
            query={},
            payload_sha256=_EMPTY_SHA256,
            amz_date=amz_date,
        )
        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("GET", self._target.url(object_key), headers=headers) as response:
                if response.status_code != 200:
                    response.read()
                    self._raise_for_status(response, "BACKUP_DOWNLOAD_FAILED", object_key)
                with destination.open("wb") as sink:
                    for chunk in response.iter_bytes():
                        sink.write(chunk)

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if token:
                query["continuation-token"] = token
            amz_date = self._now()
            headers = signed_headers(
                self._target,
                method="GET",
                object_key="",
                query=query,
                payload_sha256=_EMPTY_SHA256,
                amz_date=amz_date,
            )
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(self._target.url("", query), headers=headers)
            self._raise_for_status(response, "BACKUP_LIST_FAILED", prefix)
            keys.extend(parse_list_objects_v2(response.content))
            token = _next_continuation_token(response.content)
            if not token:
                return keys

    def delete(self, object_key: str) -> None:
        amz_date = self._now()
        headers = signed_headers(
            self._target,
            method="DELETE",
            object_key=object_key,
            query={},
            payload_sha256=_EMPTY_SHA256,
            amz_date=amz_date,
        )
        with httpx.Client(timeout=self._timeout) as client:
            response = client.delete(self._target.url(object_key), headers=headers)
        if response.status_code not in (200, 204):
            self._raise_for_status(response, "BACKUP_DELETE_FAILED", object_key)

    @staticmethod
    def _raise_for_status(response: httpx.Response, error_code: str, subject: str) -> None:
        if response.status_code >= 300:
            # Never log the signed URL or response body — only the status and the object key.
            raise BackupError(
                error_code, f"{subject}: object storage returned {response.status_code}"
            )


def _next_continuation_token(payload: bytes) -> str | None:
    root = ElementTree.fromstring(payload)
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "NextContinuationToken" and element.text:
            return element.text
    return None


# --------------------------------------------------------------------------------------------
# Subprocess steps (pg_dump, openssl) — thin, side-effecting, verified in Docker
# --------------------------------------------------------------------------------------------


def run_pg_dump(dsn: str, destination: Path, pg_dump_binary: str) -> None:
    """Write a plain-SQL dump. Fails loudly; never leaves a partial file passed as complete."""

    try:
        with destination.open("wb") as sink:
            completed = subprocess.run(
                [pg_dump_binary, "--no-owner", "--no-privileges", "--format=plain", dsn],
                stdout=sink,
                stderr=subprocess.PIPE,
                check=False,
            )
    except FileNotFoundError as exc:
        raise BackupError("BACKUP_PG_DUMP_MISSING", f"{pg_dump_binary} not found") from exc
    if completed.returncode != 0:
        raise BackupError(
            "BACKUP_PG_DUMP_FAILED",
            f"pg_dump exited {completed.returncode}: {completed.stderr.decode(errors='replace')[:500]}",
        )


def encrypt_file(source: Path, destination: Path, passphrase: str, openssl_binary: str) -> None:
    """AES-256-CBC + PBKDF2 via openssl. Passphrase is passed by env, never on argv or disk."""

    try:
        completed = subprocess.run(
            [
                openssl_binary,
                "enc",
                "-aes-256-cbc",
                "-pbkdf2",
                "-salt",
                "-in",
                str(source),
                "-out",
                str(destination),
                "-pass",
                "env:BACKUP_ENC_PASS",
            ],
            env={**os.environ, "BACKUP_ENC_PASS": passphrase},
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError("BACKUP_OPENSSL_MISSING", f"{openssl_binary} not found") from exc
    if completed.returncode != 0:
        raise BackupError(
            "BACKUP_ENCRYPT_FAILED",
            f"openssl exited {completed.returncode}: {completed.stderr.decode(errors='replace')[:200]}",
        )


def decrypt_file(source: Path, destination: Path, passphrase: str, openssl_binary: str) -> None:
    """Inverse of ``encrypt_file`` — used by the restore rehearsal."""

    try:
        completed = subprocess.run(
            [
                openssl_binary,
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-in",
                str(source),
                "-out",
                str(destination),
                "-pass",
                "env:BACKUP_ENC_PASS",
            ],
            env={**os.environ, "BACKUP_ENC_PASS": passphrase},
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError("BACKUP_OPENSSL_MISSING", f"{openssl_binary} not found") from exc
    if completed.returncode != 0:
        raise BackupError(
            "BACKUP_DECRYPT_FAILED",
            f"openssl exited {completed.returncode}: {completed.stderr.decode(errors='replace')[:200]}",
        )


def _gzip_file(source: Path, destination: Path) -> None:
    with source.open("rb") as raw, gzip.open(destination, "wb") as gz:
        for chunk in iter(lambda: raw.read(1 << 20), b""):
            gz.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------


def run_backup(settings: Settings, config: BackupConfig, *, moment: datetime) -> str:
    """Produce, verify, encrypt, upload, and prune one backup. Returns the uploaded key.

    Every local artifact lives under one temporary directory that is removed in ``finally``, so
    no plaintext or ciphertext copy is left on the server's own disk.
    """

    target = S3Target.from_settings(settings)
    store = BackupObjectStore(target, timeout_seconds=config.s3_timeout_seconds)
    object_key = backup_object_key(config.s3_prefix, moment)
    dsn = libpq_dsn(settings.database_url)

    with tempfile.TemporaryDirectory(prefix="sp-backup-") as tmp:
        workdir = Path(tmp)
        sql_path = workdir / "dump.sql"
        gz_path = workdir / "dump.sql.gz"
        enc_path = workdir / "dump.sql.gz.enc"

        run_pg_dump(dsn, sql_path, config.pg_dump_binary)
        assert_no_plaintext_secrets(sql_path.read_text(encoding="utf-8", errors="replace"))
        _gzip_file(sql_path, gz_path)
        encrypt_file(gz_path, enc_path, config.encryption_key, config.openssl_binary)

        ciphertext_bytes = enc_path.stat().st_size
        ciphertext_sha256 = _sha256(enc_path)
        store.put_file(object_key, enc_path)

    pruned = _prune(store, config, moment)
    log.info(
        "db_backup_succeeded",
        object_key=object_key,
        bucket=target.bucket,
        ciphertext_bytes=ciphertext_bytes,
        ciphertext_sha256=ciphertext_sha256,
        pruned_backups=pruned,
    )
    return object_key


def _prune(store: BackupObjectStore, config: BackupConfig, moment: datetime) -> int:
    keys = store.list_keys(config.s3_prefix)
    expired = select_expired_keys(
        keys, moment, config.retention_daily_days, config.retention_weekly_weeks
    )
    for key in expired:
        store.delete(key)
    return len(expired)


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        config = BackupConfig.from_env()
        run_backup(settings, config, moment=datetime.now(UTC))
    except BackupError as exc:
        log.error("db_backup_failed", error_code=exc.error_code, error=str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - a failed backup must never exit 0
        log.error("db_backup_failed", error_code="BACKUP_UNEXPECTED_ERROR", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
