"""The signature scrubber has to hold for loggers nobody registered.

W01 proved that application logs and database rows carry no signed URL. The surface it never
touched was a *library* logger: in a real MinIO multipart run httpx wrote the whole presigned
URL at INFO, credential and signature included. These tests pin the replacement guard —
process-wide, logger-independent, and effective before any handler sees the record.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

import pytest
import structlog

from app.core.logging import (
    REDACTED,
    install_signature_redaction,
    redact_sensitive_values,
    redact_signature_material,
)

SIGNATURE = "0123456789abcdef" * 4
CREDENTIAL = "SPAKIAEXAMPLEKEY%2F20260731%2Fus-east-1%2Fs3%2Faws4_request"
PRESIGNED = (
    "http://minio:9000/socialpilot-media/tenant/asset.mp4"
    "?uploadId=upload-1&partNumber=1&X-Amz-Algorithm=AWS4-HMAC-SHA256"
    f"&X-Amz-Credential={CREDENTIAL}&X-Amz-Date=20260731T101500Z&X-Amz-Expires=900"
    f"&X-Amz-SignedHeaders=host&X-Amz-Signature={SIGNATURE}"
)


class CapturingHandler(logging.Handler):
    """A third-party handler with its own formatter — it must never see the signature."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


class Url:
    """Stand-in for `httpx.URL`: a non-string argument whose `str()` carries the secret."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


@pytest.fixture
def captured() -> Generator[CapturingHandler]:
    install_signature_redaction()
    handler = CapturingHandler()
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def test_signature_and_credential_values_are_masked_while_the_request_stays_readable() -> None:
    scrubbed = redact_signature_material(PRESIGNED)

    assert SIGNATURE not in scrubbed
    assert CREDENTIAL not in scrubbed
    assert f"X-Amz-Signature={REDACTED}" in scrubbed
    assert f"X-Amz-Credential={REDACTED}" in scrubbed
    # The half that makes a log line useful survives: which object, which upload, which part.
    assert "socialpilot-media/tenant/asset.mp4" in scrubbed
    assert "uploadId=upload-1" in scrubbed
    assert "partNumber=1" in scrubbed
    # Non-secret SigV4 parameters are not collateral damage.
    assert "X-Amz-SignedHeaders=host" in scrubbed
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in scrubbed


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        # Google Cloud Storage and Azure sign with different parameter names; an adapter for
        # either would otherwise walk straight past a guard that only knows about `X-Amz-`.
        ("https://storage.example/o/key?GoogleAccessId=a&Signature=" + SIGNATURE, SIGNATURE),
        ("https://blob.example/c/key?sv=2024-11-04&sig=" + SIGNATURE, SIGNATURE),
        ("https://example/callback?access_token=" + SIGNATURE, SIGNATURE),
        ("https://s3.example/key?X-Amz-Security-Token=" + SIGNATURE, SIGNATURE),
    ],
)
def test_other_providers_signing_parameters_are_masked_too(text: str, secret: str) -> None:
    assert secret not in redact_signature_material(text)


def test_a_logger_nobody_registered_cannot_leak_a_signed_url(
    captured: CapturingHandler,
) -> None:
    """The guard is not a list of known-noisy loggers; it sits under all of them."""

    logging.getLogger("some.vendor.sdk.v3").info(
        'HTTP Request: %s %s "%s %d"', "PUT", Url(PRESIGNED), "HTTP/1.1", 200
    )

    assert captured.lines, "the synthetic record never reached the handler"
    assert SIGNATURE not in "\n".join(captured.lines)
    assert CREDENTIAL not in "\n".join(captured.lines)
    # Positive control: the record really did carry the URL and really was rewritten.
    assert f"X-Amz-Signature={REDACTED}" in "\n".join(captured.lines)


def test_a_signed_url_inside_an_exception_message_is_masked(captured: CapturingHandler) -> None:
    try:
        raise RuntimeError(f"upload failed for {PRESIGNED}")
    except RuntimeError:
        logging.getLogger("some.vendor.sdk.v3").exception("storage call failed")

    assert SIGNATURE not in "\n".join(captured.lines)


def test_a_malformed_format_string_is_left_for_the_handler_to_report() -> None:
    """Scrubbing must not swallow a broken log call and quietly rewrite it."""

    install_signature_redaction()

    record = logging.getLogRecordFactory()(
        "some.vendor.sdk.v3", logging.INFO, __file__, 1, "value=%d", ("not-a-number",), None
    )

    assert record.msg == "value=%d"
    assert record.args == ("not-a-number",)


def test_structlog_events_are_scrubbed_even_under_an_innocent_key() -> None:
    event: dict[str, Any] = {
        "event": f"storage_request url={PRESIGNED}",
        "location": PRESIGNED,
        "nested": {"attempts": [{"target": PRESIGNED}]},
        "authorization": "Bearer real-token",
    }

    scrubbed = redact_sensitive_values(None, "info", event)

    rendered = repr(scrubbed)
    assert SIGNATURE not in rendered
    assert CREDENTIAL not in rendered
    assert scrubbed["authorization"] == REDACTED


def test_installing_the_scrubber_twice_does_not_stack_factories() -> None:
    install_signature_redaction()
    first = logging.getLogRecordFactory()

    install_signature_redaction()

    assert logging.getLogRecordFactory() is first


def test_the_structlog_processor_is_still_wired_into_the_configured_chain() -> None:
    """A scrubber that is defined but not installed is the failure mode that started this."""

    from app.core.logging import configure_logging

    configure_logging("INFO")

    assert redact_sensitive_values in structlog.get_config()["processors"]
