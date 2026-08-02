"""Fixture and disabled adapters for the `visual_qc` capability (PRD §19.4's model checks).

Four of §19.4's checks cannot be answered by arithmetic over a container: whether the logo is
actually visible, whether the frame carries sensitive or inappropriate content, whether a face
came out distorted, whether a generated scene changed the product's shape. They are vision
questions, and the real provider arrives after W08's benchmark picks one.

The `disabled` adapter is the one that matters today, and it is the reason this capability is
absent from `reject_non_production_adapters`. The fixture's output is not a video or a piece of
prose — it is an **approval**. "No sensitive content, faces intact, logo visible" is precisely
the sentence a reviewer would act on, and a deployment that produced it from a fixture would be
manufacturing the confidence QC exists to establish. So production declines every call with a
documented code, and the four checks land in the report as `unknown`, which under the fail-closed
rule means no render is ever automatically marked `passed` until a real provider is connected.
That consequence is intended and visible rather than papered over.

The fixture exists for the pipeline around the provider: cost ceilings, route snapshots, usage
attribution, and the folding of findings onto checks. Its hooks produce the answers a real
provider eventually will — a failing check, a partial answer, a transient outage — because those
are the paths that are otherwise never exercised.
"""

from __future__ import annotations

from app.core.config import Settings
from app.modules.content.qc import (
    MODEL_CHECKS,
    CheckStatus,
    ProviderDescriptor,
    QcCheck,
    VisualQcDisabledError,
    VisualQcFinding,
    VisualQcPermanentError,
    VisualQcPort,
    VisualQcReport,
    VisualQcRequest,
    VisualQcTransientError,
)

FIXTURE_PROVIDER = "fake-visual-qc"
FIXTURE_MODEL = "fixture-vision-1"
FIXTURE_CURRENCY = "TRY"
# Confidence a fixture is entitled to claim. Deliberately not 1.0: nothing in this adapter looked
# at a pixel, and a report that reads "certain" would be lying in a field a human might trust.
FIXTURE_CONFIDENCE = 0.5


class FakeVisualQcAdapter(VisualQcPort):
    """Answer the model checks deterministically, with hooks for the answers that are not clean."""

    def __init__(
        self,
        settings: Settings,
        *,
        fail_checks: tuple[QcCheck, ...] = (),
        omit_checks: tuple[QcCheck, ...] = (),
        failure: Exception | None = None,
        cost_minor: int = 0,
    ) -> None:
        _reject_production(settings)
        self._settings = settings
        self._fail_checks = fail_checks
        self._omit_checks = omit_checks
        self._failure = failure
        self._cost_minor = cost_minor

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider=FIXTURE_PROVIDER,
            model=FIXTURE_MODEL,
            currency=FIXTURE_CURRENCY,
            estimated_cost_minor=self._cost_minor,
            enabled=True,
        )

    async def inspect(self, *, request: VisualQcRequest, timeout_seconds: int) -> VisualQcReport:
        if self._failure is not None:
            raise self._failure
        if not request.frames:
            # No frames means nothing was inspected. Returning "all clear" here would be the
            # exact failure this capability's disabled adapter exists to prevent, one layer down.
            raise VisualQcPermanentError("QC_VISUAL_NO_FRAMES")
        findings = tuple(
            VisualQcFinding(
                check=check,
                status=(CheckStatus.FAILED if check in self._fail_checks else CheckStatus.PASSED),
                confidence=FIXTURE_CONFIDENCE,
                code=f"QC_VISUAL_{check.value.upper()}_REJECTED"
                if check in self._fail_checks
                else None,
            )
            for check in request.checks
            # A logo-visibility answer about a render with no logo would be an invented finding.
            if check not in self._omit_checks
            and (check is not QcCheck.LOGO_VISIBLE or request.expects_logo)
        )
        return VisualQcReport(
            provider=FIXTURE_PROVIDER,
            model=FIXTURE_MODEL,
            findings=findings,
            actual_cost_minor=self._cost_minor,
            currency=FIXTURE_CURRENCY,
        )


class DisabledVisualQcAdapter(VisualQcPort):
    """Decline every inspection with a documented code, at call time rather than at startup."""

    def __init__(self, *, reason: str) -> None:
        self._reason = reason

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="disabled",
            model="disabled",
            currency=FIXTURE_CURRENCY,
            estimated_cost_minor=0,
            enabled=False,
        )

    async def inspect(self, *, request: VisualQcRequest, timeout_seconds: int) -> VisualQcReport:
        raise VisualQcDisabledError(self._reason)


def _reject_production(settings: Settings) -> None:
    """The fixture cannot be constructed in production, even by a caller that bypasses the factory.

    `create_visual_qc` already swaps production onto the disabled adapter. This is the second
    gate the infrastructure layer keeps on every `fake_*` adapter, and it is not redundant: the
    factory protects the composition root, this protects every other construction site.
    """

    if settings.app_env == "production":
        raise RuntimeError("VISUAL_QC_FIXTURE_NOT_ALLOWED_IN_PRODUCTION")


def model_checks_for(*, expects_logo: bool) -> tuple[QcCheck, ...]:
    """Which model checks a request asks for. Logo visibility only when a logo was drawn.

    Every other model check applies to any frame, so the set is `MODEL_CHECKS` minus the one
    question a render without a logo cannot answer. The caller still reports that check — as
    `passed` with `applicable: false` — rather than dropping it from the report.
    """

    return tuple(
        check for check in MODEL_CHECKS if check is not QcCheck.LOGO_VISIBLE or expects_logo
    )


__all__ = [
    "DisabledVisualQcAdapter",
    "FakeVisualQcAdapter",
    "VisualQcPermanentError",
    "VisualQcTransientError",
    "model_checks_for",
]
