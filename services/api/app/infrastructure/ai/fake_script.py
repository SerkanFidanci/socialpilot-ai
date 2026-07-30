"""Deterministic non-production adapters for the `script_generation` capability.

`FakeScriptGenerationAdapter` writes a valid script from the brief it is handed, using the slot
tokens it was offered and no figures of its own. It is a fixture, not a model: the point of the
slice is the pipeline around the provider, and a deterministic provider is what lets a test
assert that a *hostile* response is rejected — including responses no real model would produce.

`DisabledScriptGenerationAdapter` is the production answer. Serving fixture prose as real
marketing copy is the one outcome that must be impossible, so production gets an adapter that
refuses rather than a fake that complies. It refuses on call rather than at import, which is
why a deployment without a real provider still boots and still serves every other endpoint.
"""

from __future__ import annotations

import json
from typing import Any, Final, Literal

from app.core.config import Settings
from app.modules.content.script import (
    ProviderDescriptor,
    ScriptGenerationDisabledError,
    ScriptGenerationPermanentError,
    ScriptGenerationPort,
    ScriptGenerationRequest,
    ScriptGenerationResult,
    ScriptGenerationTransientError,
)

FAKE_PROVIDER: Final = "fake"
FAKE_MODEL: Final = "fake-script-v1"
DISABLED_PROVIDER: Final = "disabled"
DISABLED_MODEL: Final = "none"


class FakeScriptGenerationAdapter(ScriptGenerationPort):
    """Fixture-only provider covering the success path and every explicit failure path.

    `output_json` overrides the generated response verbatim, which is how the suite feeds in
    malformed JSON, an extra field, an invented price and a free-text CTA without needing a
    separate stub class per case.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        output_json: str | None = None,
        failure: Literal["transient", "permanent"] | None = None,
        echo_untrusted_notes: bool = False,
        estimated_cost_minor: int = 0,
        actual_cost_minor: int = 0,
        currency: str = "TRY",
        provider: str = FAKE_PROVIDER,
        model: str = FAKE_MODEL,
    ) -> None:
        _reject_production(settings)
        # Public and mutable: a test seeds its tenant first, then decides what the provider
        # should answer with. Rebuilding the adapter afterwards would mean rebuilding the app.
        self.output_json = output_json
        self.failure = failure
        self._echo_untrusted_notes = echo_untrusted_notes
        self._descriptor = ProviderDescriptor(
            provider=provider,
            model=model,
            currency=currency,
            estimated_cost_minor=estimated_cost_minor,
            enabled=True,
        )
        self._actual_cost_minor = actual_cost_minor
        # Tests assert on these: "the ceiling stopped the call" is only meaningful if the call
        # provably did not happen.
        self.calls = 0
        self.last_request: ScriptGenerationRequest | None = None

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def generate(
        self, *, request: ScriptGenerationRequest, timeout_seconds: int
    ) -> ScriptGenerationResult:
        if timeout_seconds < 1:
            raise ScriptGenerationPermanentError("SCRIPT_GENERATION_TIMEOUT_INVALID")
        self.calls += 1
        self.last_request = request
        if self.failure == "transient":
            raise ScriptGenerationTransientError("SCRIPT_PROVIDER_UNAVAILABLE")
        if self.failure == "permanent":
            raise ScriptGenerationPermanentError("SCRIPT_PROVIDER_REJECTED_REQUEST")
        output = self.output_json or self._compose(request)
        return ScriptGenerationResult(
            provider=self._descriptor.provider,
            model=self._descriptor.model,
            output_json=output,
            actual_cost_minor=self._actual_cost_minor,
            currency=self._descriptor.currency,
        )

    def _compose(self, request: ScriptGenerationRequest) -> str:
        """Write a valid script from the offered slots, inventing no figure of its own."""

        data = request.input_data
        product = _mapping(data.get("product"))
        name = str(product.get("name") or "ürün")
        tokens = {
            str(offer.get("kind")): str(offer.get("token"))
            for offer in _sequence(data.get("verified_slots"))
        }
        price_token = tokens.get("price", "")
        cta_token = tokens.get("cta", "")
        campaign_token = tokens.get("campaign_end", "")

        hook = f"{name} bugün hazır."
        if self._echo_untrusted_notes:
            # The obedient-model case. It exists so a test can prove the *pipeline* rejects an
            # injected instruction even when the provider follows it — the guarantee cannot rest
            # on the provider declining.
            notes = _mapping(data.get("untrusted_media_notes"))
            echoed = " ".join(
                str(_mapping(item).get("text", "")) for item in _sequence(notes.get("items"))
            )
            hook = f"{hook} {echoed}".strip()

        offer_line = f"Şimdi {price_token}.".strip() if price_token else f"{name} tezgâhta."
        if campaign_token:
            offer_line = f"{offer_line} Son gün {campaign_token}.".strip()
        segments = [
            {
                "purpose": "hook",
                "voice_text": hook,
                "required_scene_tags": ["product_closeup"],
                "target_duration_ms": 2500,
            },
            {
                "purpose": "process",
                "voice_text": "Her sipariş özenle hazırlanıyor.",
                "required_scene_tags": ["preparation"],
                "target_duration_ms": 4500,
            },
            {
                "purpose": "offer",
                "voice_text": offer_line,
                "required_scene_tags": ["product_closeup"],
                "target_duration_ms": 4000,
            },
        ]
        document: dict[str, Any] = {
            "hook": {"text": hook, "duration_ms": 2500},
            "segments": segments,
            "cta": {
                "source": "approved_cta",
                # The token is `{{cta:<uuid>}}`; the contract wants the bare id.
                "reference_id": cta_token.removeprefix("{{cta:").removesuffix("}}"),
            },
        }
        return json.dumps(document, ensure_ascii=False)


class DisabledScriptGenerationAdapter(ScriptGenerationPort):
    """Declines every call with a documented reason. Production's adapter until a real one lands."""

    def __init__(self, *, reason: str) -> None:
        self._reason = reason

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider=DISABLED_PROVIDER,
            model=DISABLED_MODEL,
            currency="TRY",
            estimated_cost_minor=0,
            enabled=False,
        )

    async def generate(
        self, *, request: ScriptGenerationRequest, timeout_seconds: int
    ) -> ScriptGenerationResult:
        del request, timeout_seconds
        raise ScriptGenerationDisabledError(self._reason)


def _reject_production(settings: Settings) -> None:
    if settings.app_env == "production":
        raise RuntimeError("SCRIPT_GENERATION_FAKE_ADAPTER_NOT_ALLOWED_IN_PRODUCTION")


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
