"""The signature scrubber has to hold for loggers nobody registered.

W01 proved that application logs and database rows carry no signed URL. The surface it never
touched was a *library* logger: in a real MinIO multipart run httpx wrote the whole presigned
URL at INFO, credential and signature included. These tests pin the replacement guard —
process-wide, logger-independent, and effective before any handler sees the record.

W16 adds the surface W14's record factory could not reach. `Logger.makeRecord` copies
`extra={...}` onto the record *after* the factory has returned, so a handler formatting
`%(url)s` printed the raw signature while the message itself was masked. Codex reproduced that
in the API and in the worker image, with a plain string, with an `httpx.URL` object, and inside
a nested dict. Every one of those is a numbered test below, on a logger and a handler that were
both created after the guard was installed.
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import subprocess
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import structlog

from app.core.logging import (
    _CANDIDATE_MARKERS,
    _SIGNED_QUERY_PARAMS,
    REDACTED,
    install_signature_redaction,
    redact_sensitive_values,
    redact_signature_material,
)

API_ROOT = Path(__file__).resolve().parents[2]

SIGNATURE = "0123456789abcdef" * 4
CREDENTIAL = "SPAKIAEXAMPLEKEY%2F20260731%2Fus-east-1%2Fs3%2Faws4_request"
PRESIGNED = (
    "http://minio:9000/socialpilot-media/tenant/asset.mp4"
    "?uploadId=upload-1&partNumber=1&X-Amz-Algorithm=AWS4-HMAC-SHA256"
    f"&X-Amz-Credential={CREDENTIAL}&X-Amz-Date=20260731T101500Z&X-Amz-Expires=900"
    f"&X-Amz-SignedHeaders=host&X-Amz-Signature={SIGNATURE}"
)

# One URL per provider the adapter list can be pointed at, each carrying its own sentinel, so a
# failure names the parameter that leaked instead of "something leaked".
PROVIDER_URLS = (
    ("X-Amz-Signature", f"https://s3.example/key?X-Amz-Signature={SIGNATURE}"),
    ("X-Amz-Credential", f"https://s3.example/key?X-Amz-Credential={SIGNATURE}"),
    ("X-Amz-Security-Token", f"https://s3.example/key?X-Amz-Security-Token={SIGNATURE}"),
    ("Signature", f"https://storage.example/o/key?Signature={SIGNATURE}"),
    ("GoogleAccessId", f"https://storage.example/o/key?GoogleAccessId={SIGNATURE}"),
    ("sig", f"https://blob.example/c/key?sv=2024-11-04&sig={SIGNATURE}"),
)


class CapturingHandler(logging.Handler):
    """A third-party handler with its own formatter — it must never see the signature."""

    def __init__(self, fmt: str = "%(name)s %(levelname)s %(message)s") -> None:
        super().__init__(level=logging.NOTSET)
        self.setFormatter(logging.Formatter(fmt))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


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


@contextmanager
def fresh_logger(name: str, fmt: str) -> Iterator[tuple[logging.Logger, CapturingHandler]]:
    """A logger *and* a handler created after installation, with a format that reads `extra`.

    Both halves matter. The logger is new, so the guard cannot be a list of known-noisy names;
    the handler is new and formats an `extra` field itself, which is exactly what Codex attached
    to reproduce the leak. It does not propagate, so an unrelated library log line cannot hit a
    format string that requires `url`.
    """

    install_signature_redaction()
    handler = CapturingHandler(fmt)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)


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


def test_installing_the_scrubber_twice_does_not_stack_hooks() -> None:
    install_signature_redaction()
    hooks = (logging.getLogRecordFactory(), logging.Logger.callHandlers, logging.Handler.handle)

    install_signature_redaction()

    assert (
        logging.getLogRecordFactory(),
        logging.Logger.callHandlers,
        logging.Handler.handle,
    ) == hooks


def test_the_structlog_processor_is_still_wired_into_the_configured_chain() -> None:
    """A scrubber that is defined but not installed is the failure mode that started this."""

    from app.core.logging import configure_logging

    configure_logging("INFO")

    assert redact_sensitive_values in structlog.get_config()["processors"]


# --- the `extra` surface (W16 criterion 1) ----------------------------------------------------


def test_a_signed_url_passed_as_a_plain_string_in_extra_is_masked() -> None:
    with fresh_logger("w16.extra.string", "%(message)s extra=%(url)s") as (logger, handler):
        logger.info("upload part", extra={"url": PRESIGNED})

    assert handler.lines, "the record never reached the handler"
    assert SIGNATURE not in handler.text
    assert CREDENTIAL not in handler.text
    # Positive control: the handler really did format the extra field, and it was rewritten.
    assert f"X-Amz-Signature={REDACTED}" in handler.text


def test_a_url_object_in_extra_is_masked_even_though_it_is_not_a_string() -> None:
    """`httpx.URL` is the shape that actually appears; a `str`-only scrub would walk past it."""

    with fresh_logger("w16.extra.object", "%(message)s extra=%(url)s") as (logger, handler):
        logger.info("upload part", extra={"url": Url(PRESIGNED)})

    assert SIGNATURE not in handler.text
    assert f"X-Amz-Signature={REDACTED}" in handler.text


def test_a_signed_url_nested_inside_an_extra_dict_is_masked() -> None:
    with fresh_logger("w16.extra.nested", "%(message)s payload=%(payload)s") as (logger, handler):
        logger.info(
            "upload part",
            extra={"payload": {"request": {"targets": [Url(PRESIGNED)], "attempt": 2}}},
        )

    assert SIGNATURE not in handler.text
    assert f"X-Amz-Signature={REDACTED}" in handler.text
    # The walk rebuilds the container rather than stringifying it, so the field is still a dict.
    assert "'attempt': 2" in handler.text


def test_logging_a_value_does_not_mutate_the_caller_s_object() -> None:
    """A record does not own what it was handed; the reference on the record is what changes."""

    payload = {"targets": [PRESIGNED]}
    url = Url(PRESIGNED)

    with fresh_logger("w16.extra.purity", "%(payload)s %(url)s") as (logger, handler):
        logger.info("upload part", extra={"payload": payload, "url": url})

    assert SIGNATURE not in handler.text
    assert payload == {"targets": [PRESIGNED]}
    assert str(url) == PRESIGNED


def test_an_extra_value_with_nothing_to_hide_keeps_its_type() -> None:
    """Scrubbing must not turn every field into a string: `%(part)d` has to keep working."""

    with fresh_logger("w16.extra.types", "part=%(part)d ratio=%(ratio).1f") as (logger, handler):
        logger.info("upload part", extra={"part": 7, "ratio": 0.5})

    assert handler.text == "part=7 ratio=0.5"


def test_a_value_nested_past_the_walk_limit_is_still_masked() -> None:
    """Below the depth ceiling the value is rendered once and scrubbed as text, never skipped.

    The ceiling bounds the *walk*, not the guarantee: what a handler formats is the rendered
    container, and that rendering is scrubbed as a whole before it reaches one.
    """

    deep: Any = PRESIGNED
    for _ in range(8):
        deep = {"next": deep}

    with fresh_logger("w16.extra.deep", "%(payload)s") as (logger, handler):
        logger.info("upload part", extra={"payload": deep})

    assert SIGNATURE not in handler.text
    assert REDACTED in handler.text


def test_a_record_handed_straight_to_a_handler_is_still_scrubbed() -> None:
    """The path with no logger in it at all — found by attacking this fix, then closed.

    `Logger.callHandlers` is the barrier for everything that was logged; a record built by hand
    and passed to `Handler.handle` never reaches it. A log-receiving server and a queue listener
    both do exactly that, so the handler's own entry point is the backstop.
    """

    handler = CapturingHandler("%(message)s extra=%(url)s")
    install_signature_redaction()
    record = logging.getLogRecordFactory()(
        "w16.no.logger", logging.INFO, __file__, 1, "PUT %s", (PRESIGNED,), None
    )
    record.url = PRESIGNED

    handler.handle(record)

    assert SIGNATURE not in handler.text
    assert handler.text.count(f"X-Amz-Signature={REDACTED}") == 2


def test_a_container_shape_the_walk_does_not_know_is_scrubbed_as_text() -> None:
    """A `set` is neither a mapping nor a sequence here; it still cannot carry a signature out."""

    with fresh_logger("w16.extra.set", "%(targets)s") as (logger, handler):
        logger.info("upload part", extra={"targets": {PRESIGNED}})

    assert SIGNATURE not in handler.text
    assert REDACTED in handler.text


@pytest.mark.parametrize(
    ("parameter", "url"), PROVIDER_URLS, ids=[name for name, _ in PROVIDER_URLS]
)
def test_every_signing_parameter_is_masked_on_both_the_message_and_the_extra_surface(
    parameter: str, url: str
) -> None:
    """The inventory, counted: S3's three, GCS's two, Azure's one — twice each."""

    with fresh_logger(f"w16.inventory.{parameter}", "%(message)s extra=%(url)s") as (
        logger,
        handler,
    ):
        logger.info("call to %s", Url(url), extra={"url": Url(url)})

    assert SIGNATURE not in handler.text
    assert handler.text.count(f"{parameter}={REDACTED}") == 2


def test_the_fast_path_cannot_hide_a_parameter_from_the_scrubber() -> None:
    """The cheap pre-filter skips a line on a two-branch argument; this pins the first branch.

    A parameter name written *literally* contains one of the markers. The second branch — a name
    written any other way needs a `%` to do it — is pinned by the percent-encoding tests below.
    Together they are why skipping a line is safe. Adding a parameter that satisfies neither
    would be a silent false negative rather than a broken build, so it fails here instead.
    """

    missed = [name for name in _SIGNED_QUERY_PARAMS if _CANDIDATE_MARKERS.search(name) is None]

    assert missed == []


def test_a_line_with_no_signing_material_is_returned_untouched() -> None:
    ordinary = "GET /v1/businesses/1/media 200 in 12ms user=owner count=3"

    assert redact_signature_material(ordinary) is ordinary


# --- percent-encoded parameter names (W16 fix round 2) ----------------------------------------
#
# `X-Amz-%53ignature` is `X-Amz-Signature` to `urllib.parse.parse_qsl` and to a server, but it
# carries no `sig` for the fast path to find, so the whole scrub was skipped. Codex got a raw
# sentinel out of a QueueHandler and out of `extra` that way. The escape is unbounded in depth —
# `%2553` decodes to `%53` decodes to `S` — so the fix has to be a rule, not two decode rounds.

ENCODED_NAMES = [
    ("single encoding", f"?X-Amz-%53ignature={SIGNATURE}"),
    ("lowercase hex", f"?X-Amz-%73ignature={SIGNATURE}"),
    ("double encoding", f"?X-Amz-%2553ignature={SIGNATURE}"),
    ("triple encoding", f"?X-Amz-%252553ignature={SIGNATURE}"),
    ("first character encoded", f"?%58-Amz-Signature={SIGNATURE}"),
    ("every character encoded", f"?%73%69%67={SIGNATURE}"),
    ("encoded separator", f"?X-Amz-Signature%3D{SIGNATURE}"),
    ("credential", f"?X-Amz-%43redential={SIGNATURE}"),
    ("google access id", f"?%47oogleAccessId={SIGNATURE}"),
    ("azure sig", f"?sv=2024-11-04&%73ig={SIGNATURE}"),
]


@pytest.mark.parametrize(
    "url", [url for _, url in ENCODED_NAMES], ids=[label for label, _ in ENCODED_NAMES]
)
def test_a_percent_encoded_parameter_name_still_loses_its_value(url: str) -> None:
    scrubbed = redact_signature_material(url)

    assert SIGNATURE not in scrubbed
    # The name is left in the form it arrived in — masking is applied to the raw text, so the
    # log line still reads as the request that was actually made.
    assert REDACTED in scrubbed


@pytest.mark.parametrize(
    "url", [url for _, url in ENCODED_NAMES], ids=[label for label, _ in ENCODED_NAMES]
)
def test_a_percent_encoded_parameter_name_is_masked_on_the_extra_surface_too(url: str) -> None:
    with fresh_logger("w16.encoded.extra", "%(message)s extra=%(url)s") as (logger, handler):
        logger.info("call to %s", Url(url), extra={"url": Url(url)})

    assert SIGNATURE not in handler.text


def test_a_percent_encoded_parameter_name_cannot_ride_a_queue_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex's repro used a QueueHandler, so the regression test uses one too."""

    install_signature_redaction()
    records: queue.Queue[logging.LogRecord] = queue.Queue()
    handler = CapturingHandler("%(message)s extra=%(url)s")
    listener = logging.handlers.QueueListener(records, handler)
    listener.start()
    logger = logging.getLogger("w16.encoded.queue")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(logging.handlers.QueueHandler(records))
    try:
        url = f"?X-Amz-%53ignature={SIGNATURE}&X-Amz-%43redential={SIGNATURE}"
        logger.info("call to %s", Url(url), extra={"url": Url(url)})
    finally:
        listener.stop()
        logger.handlers.clear()

    assert handler.lines, "the record never reached the listener's handler"
    assert SIGNATURE not in handler.text


def test_an_ordinary_percent_sign_is_not_mistaken_for_an_encoded_name() -> None:
    """The wider pattern only runs when a `%` is present, so it must stay quiet on real ones."""

    line = "render progress=50% elapsed=12ms user=owner"

    assert redact_signature_material(line) == line


# --- the worker process (W16 criterion 1, worker half) ----------------------------------------

WORKER_PROBE = """
import logging, sys

from app.worker.composition import shutdown_worker_process, start_worker_process


class Url:
    def __init__(self, value): self._value = value
    def __str__(self): return self._value


start_worker_process()
try:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s | url=%(url)s | payload=%(payload)s"))
    logger = logging.getLogger("worker.vendor.sdk")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    logger.info(
        "PUT %s",
        Url(SIGNED),
        extra={"url": Url(SIGNED), "payload": {"request": {"target": SIGNED}}},
    )
finally:
    shutdown_worker_process()
"""


def test_the_worker_process_masks_the_extra_surface_too() -> None:
    """Run the worker's own process init in a fresh interpreter and log the way Codex did.

    A subprocess rather than an in-process call: the worker is the one process that never calls
    `configure_logging`, so the claim under test is that *its* init installs the guard — not that
    this pytest session happens to have installed it already.
    """

    probe = f"SIGNED = {PRESIGNED!r}\n" + WORKER_PROBE
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert SIGNATURE not in result.stdout
    assert CREDENTIAL not in result.stdout
    # Positive control on all three surfaces the worker was shown to leak from.
    assert result.stdout.count(f"X-Amz-Signature={REDACTED}") == 3
