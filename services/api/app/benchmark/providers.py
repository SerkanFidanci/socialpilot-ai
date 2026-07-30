"""Providers under test and the registry that maps a capability to one.

The default set is deterministic *fake* providers, so ``make benchmark`` exercises the harness
itself with no credentials and no network. Each capability gets its own descriptor, because in
production different capabilities route to different providers in different regions with
different legal eligibility — the report has to show that variety.

A real provider set is a configuration surface only. No adapter and no key is wired here
(that is onboarding/operations work, explicitly out of W08's scope); ``build_registry`` raises
a clear message pointing at the expected configuration instead of silently doing nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.benchmark.golden import GoldenSample
from app.benchmark.model import (
    BenchmarkError,
    Capability,
    ProviderDescriptor,
    ProviderInput,
    require_minimized_input,
)

FAKE_PROVIDER_SET = "fake"

# Environment variables a future real run would read. Declared for documentation only; the
# harness places no key and contacts no provider.
REAL_PROVIDER_ENV_HINT = "BENCHMARK_REAL_PROVIDER_<CAPABILITY>_{ENDPOINT,MODEL,API_KEY,DATA_REGION}"


class BenchmarkProvider(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    def invoke(
        self, *, capability: Capability, sample: GoldenSample, provider_input: ProviderInput
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class FakeGoldenProvider:
    """Returns the sample's recorded ``fake_output``; the run still pays and is timed."""

    _descriptor: ProviderDescriptor

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def invoke(
        self, *, capability: Capability, sample: GoldenSample, provider_input: ProviderInput
    ) -> Mapping[str, object]:
        # Defence in depth: a provider must never be handed an original (§34.3).
        require_minimized_input(provider_input)
        return sample.capabilities[capability].fake_output


@dataclass(frozen=True)
class ProviderRegistry:
    name: str
    providers: Mapping[Capability, BenchmarkProvider]

    def capabilities(self) -> tuple[Capability, ...]:
        return tuple(sorted(self.providers, key=lambda capability: capability.value))

    def provider_for(self, capability: Capability) -> BenchmarkProvider:
        return self.providers[capability]


# Fixed fake descriptors. Data regions and eligibility are illustrative but chosen to make the
# legal-eligibility column meaningful: media-bearing capabilities (asr, video, tts) are marked
# ineligible for face/voice input when hosted abroad without a standard contract (KVKK), while
# pure-text capabilities carry no biometric payload.
_FAKE_DESCRIPTORS: dict[Capability, ProviderDescriptor] = {
    Capability.ASR: ProviderDescriptor(
        name="fake-asr",
        model="deterministic-asr-1",
        data_region="eu-central",
        face_voice_input_allowed=False,
        unit_cost_minor=2,
        currency="USD",
        compliance_note="Voice is biometric-adjacent; needs KVKK standard contract before use.",
    ),
    Capability.VIDEO_UNDERSTANDING: ProviderDescriptor(
        name="fake-vlm",
        model="deterministic-vlm-1",
        data_region="cn-north",
        face_voice_input_allowed=False,
        unit_cost_minor=3,
        currency="USD",
        compliance_note="Faces may appear; cross-border transfer needs standard contract + notice.",
    ),
    Capability.TEXT_STRATEGY: ProviderDescriptor(
        name="fake-text",
        model="deterministic-text-1",
        data_region="cn-north",
        face_voice_input_allowed=True,
        unit_cost_minor=1,
        currency="USD",
        compliance_note="Text only; no biometric payload.",
    ),
    Capability.STRUCTURED_TIMELINE: ProviderDescriptor(
        name="fake-timeline",
        model="deterministic-timeline-1",
        data_region="local",
        face_voice_input_allowed=True,
        unit_cost_minor=1,
        currency="USD",
        compliance_note="Structural transform; runs locally.",
    ),
    Capability.TTS: ProviderDescriptor(
        name="fake-tts",
        model="deterministic-tts-1",
        data_region="us-east",
        face_voice_input_allowed=False,
        unit_cost_minor=2,
        currency="USD",
        compliance_note="Synthesized voice output; treat as biometric-adjacent under KVKK.",
    ),
}


def build_fake_registry() -> ProviderRegistry:
    return ProviderRegistry(
        name=FAKE_PROVIDER_SET,
        providers={
            capability: FakeGoldenProvider(descriptor)
            for capability, descriptor in _FAKE_DESCRIPTORS.items()
        },
    )


def build_registry(provider_set: str) -> ProviderRegistry:
    if provider_set == FAKE_PROVIDER_SET:
        return build_fake_registry()
    raise BenchmarkError(
        f"provider set {provider_set!r} is not wired. This harness ships only the "
        f"{FAKE_PROVIDER_SET!r} set; a real provider is a configuration surface only "
        f"(expected env: {REAL_PROVIDER_ENV_HINT}). No credential is bundled — onboarding a "
        "real provider is out of W08's scope."
    )
