"""The `visual_qc` boundary: adapter selection, an honest fixture, and QC that cannot act.

The sharpest decision in slice 2D lives here. A fixture script writes prose a reviewer could
publish; a fixture *inspection* writes an approval a reviewer could act on. So production gets
the disabled adapter, the four model checks land as `unknown`, and — under the fail-closed rule —
no render is ever automatically `passed` until a real provider exists. These tests pin that
consequence in place, because it is the kind of inconvenience a future change would be tempted to
remove.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.infrastructure.ai import create_visual_qc
from app.infrastructure.ai.fake_visual_qc import (
    FIXTURE_CONFIDENCE,
    DisabledVisualQcAdapter,
    FakeVisualQcAdapter,
)
from app.infrastructure.render import create_qc_probe
from app.infrastructure.render.qc_probe import FFmpegQcProbe
from app.modules.content.qc import (
    MODEL_CHECKS,
    CheckStatus,
    QcCheck,
    VisualQcDisabledError,
    VisualQcPermanentError,
    VisualQcRequest,
)
from app.modules.content.qc_service import ContentQcReportService, ContentQcService


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def production(**overrides: object) -> Settings:
    """A production environment, assembled the only way it can be today.

    `identity_adapter` has one value and that value is refused in production, so a production
    `Settings` cannot be constructed through validation at all yet. Flipping the field afterwards
    is the device W13's and W15's suites already use, kept identical here.
    """

    configured = settings(**overrides)
    configured.app_env = "production"
    return configured


def request(*, frames: tuple[Path, ...], expects_logo: bool = True) -> VisualQcRequest:
    return VisualQcRequest(
        frames=frames,
        checks=tuple(
            check for check in MODEL_CHECKS if check is not QcCheck.LOGO_VISIBLE or expects_logo
        ),
        expects_logo=expects_logo,
        max_frames=5,
    )


# --- adapter selection ---------------------------------------------------------------------------


def test_the_factory_selects_the_fixture_outside_production_and_disabled_inside() -> None:
    assert isinstance(create_visual_qc(settings(visual_qc_adapter="fake")), FakeVisualQcAdapter)
    assert isinstance(
        create_visual_qc(settings(visual_qc_adapter="disabled")), DisabledVisualQcAdapter
    )
    assert isinstance(
        create_visual_qc(production(visual_qc_adapter="fake")), DisabledVisualQcAdapter
    )


def test_production_boot_is_not_refused_over_the_vision_adapter() -> None:
    """`VISUAL_QC_ADAPTER=fake` in production must not take the deployment down.

    The rule W13 settled and the PM generalized. The startup gate names every development-only
    adapter it refuses; the vision adapter has to be absent from that list, because it is handled
    by the factory instead. The assertion is on the *message*: it must complain about identity
    and render, and say nothing about visual QC.
    """

    with pytest.raises(ValidationError) as error:
        settings(
            app_env="production",
            identity_adapter="local",
            storage_adapter="s3",
            materializer_adapter="s3",
            render_adapter="fake",
            visual_qc_adapter="fake",
            s3_endpoint_url="https://example.invalid",
            s3_bucket="bucket",
            s3_access_key_id=SecretStr("key"),
            s3_secret_access_key=SecretStr("secret"),
            database_url="postgresql+asyncpg://user:pass@db:5432/app",
        )
    message = str(error.value)
    assert "fake render adapter" in message
    assert "visual" not in message.lower()


def test_the_fixture_adapter_refuses_to_be_constructed_in_production() -> None:
    """The factory guards the composition root; this guards every other construction site."""

    with pytest.raises(RuntimeError, match="VISUAL_QC_FIXTURE_NOT_ALLOWED_IN_PRODUCTION"):
        FakeVisualQcAdapter(production())


@pytest.mark.asyncio
async def test_the_disabled_adapter_declines_every_call() -> None:
    adapter = create_visual_qc(settings(visual_qc_adapter="disabled"))
    assert adapter.descriptor.enabled is False
    with pytest.raises(VisualQcDisabledError):
        await adapter.inspect(request=request(frames=(Path("frame.jpg"),)), timeout_seconds=1)


def test_the_measurement_probe_has_no_fixture_at_all() -> None:
    """Measurement is the guarantee; a fake probe would be a fixture verifying a fixture."""

    assert isinstance(create_qc_probe(settings()), FFmpegQcProbe)
    assert isinstance(create_qc_probe(production()), FFmpegQcProbe)


# --- the fixture is honest about being a fixture --------------------------------------------------


@pytest.mark.asyncio
async def test_the_fixture_never_claims_certainty() -> None:
    """Nothing in it looked at a pixel, and a report reading "certain" would lie in a field a
    human might act on."""

    report = await FakeVisualQcAdapter(settings()).inspect(
        request=request(frames=(Path("frame.jpg"),)), timeout_seconds=1
    )
    assert report.findings
    assert all(finding.confidence < 1.0 for finding in report.findings)
    assert all(finding.confidence == FIXTURE_CONFIDENCE for finding in report.findings)


@pytest.mark.asyncio
async def test_the_fixture_refuses_to_answer_about_frames_it_was_not_given() -> None:
    """ "All clear" about nothing is the same failure as a fixture approval, one layer down."""

    with pytest.raises(VisualQcPermanentError, match="QC_VISUAL_NO_FRAMES"):
        await FakeVisualQcAdapter(settings()).inspect(request=request(frames=()), timeout_seconds=1)


@pytest.mark.asyncio
async def test_the_fixture_does_not_invent_a_logo_finding() -> None:
    report = await FakeVisualQcAdapter(settings()).inspect(
        request=request(frames=(Path("f.jpg"),), expects_logo=False), timeout_seconds=1
    )
    assert QcCheck.LOGO_VISIBLE not in {finding.check for finding in report.findings}


@pytest.mark.asyncio
async def test_the_fixture_can_produce_the_answers_a_real_provider_eventually_will() -> None:
    report = await FakeVisualQcAdapter(
        settings(), fail_checks=(QcCheck.SENSITIVE_CONTENT,), omit_checks=(QcCheck.PRODUCT_SHAPE,)
    ).inspect(request=request(frames=(Path("f.jpg"),)), timeout_seconds=1)
    answers = {finding.check: finding for finding in report.findings}
    assert answers[QcCheck.SENSITIVE_CONTENT].status is CheckStatus.FAILED
    assert answers[QcCheck.SENSITIVE_CONTENT].code is not None
    # An omitted check is one the provider did not answer; the caller fills it with `unknown`.
    assert QcCheck.PRODUCT_SHAPE not in answers


# --- QC decides, it never acts --------------------------------------------------------------------


def test_the_qc_service_has_no_way_to_re_render_or_re_route() -> None:
    """Criterion 3's claim, enforced by the constructor rather than by intent.

    There is no render port and no script/tts port among the collaborators, so a QC run cannot
    start a render, swap a provider or generate anything — regardless of what a later
    implementation decides to do. The attempt limit that would bound such a loop belongs to
    slice 2E, and this is what keeps the loop from existing before the bound does.
    """

    parameters = set(inspect.signature(ContentQcService.__init__).parameters) - {"self"}
    assert parameters == {"session", "settings", "materializer", "probe", "visual_qc"}


def test_the_read_side_cannot_start_a_measurement() -> None:
    """A controller holding a probe could run FFmpeg inside a request. It holds a session only."""

    parameters = set(inspect.signature(ContentQcReportService.__init__).parameters) - {"self"}
    assert parameters == {"session"}


# --- the thresholds are configuration, and inconsistent configuration is refused -------------------


def test_a_silence_floor_above_the_loudness_window_is_refused_at_startup() -> None:
    """Otherwise every correctly mixed output reads as silent and the loudness check never fires."""

    with pytest.raises(ValidationError, match="QC_SILENCE_FLOOR_LUFS"):
        settings(
            qc_silence_floor_lufs=-20.0,
            qc_loudness_target_lufs=-40.0,
            qc_loudness_tolerance_lu=1.0,
        )


def test_a_qc_job_timeout_that_cannot_cover_its_own_steps_is_refused() -> None:
    with pytest.raises(ValidationError, match="QC_JOB_TIMEOUT_SECONDS"):
        settings(qc_job_timeout_seconds=10, qc_probe_timeout_seconds=180)


def test_an_unusable_source_ratio_below_the_defect_limits_is_refused() -> None:
    """The `request_new_media` split has to sit above the ordinary limits or it can never fire."""

    with pytest.raises(ValidationError, match="QC_UNUSABLE_SOURCE_RATIO"):
        settings(qc_unusable_source_ratio=0.2, qc_static_ratio_limit=0.3)


def test_the_default_thresholds_are_internally_consistent() -> None:
    configured = settings()
    assert configured.qc_silence_floor_lufs < (
        configured.qc_loudness_target_lufs - configured.qc_loudness_tolerance_lu
    )
    assert configured.qc_black_ratio_limit <= configured.qc_unusable_source_ratio
    assert configured.qc_static_ratio_limit <= configured.qc_unusable_source_ratio
    assert configured.celery_task_soft_time_limit_seconds >= configured.qc_job_timeout_seconds
