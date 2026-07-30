"""Load the committed golden set: qualities, media spec, ground truth and fake output.

The harness reads machine-readable JSON only; it never needs media bytes, which is why the
default run works in CI without generating anything. ``scripts/make_golden_media.py``
materializes the FFmpeg-producible clips for a real-provider run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.benchmark.model import BenchmarkProvenanceError, Capability

_DEFAULT_SAMPLES_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden" / "samples"
)


@dataclass(frozen=True)
class CapabilitySpec:
    capability: Capability
    prompt_version: str
    route_revision: str
    ground_truth: Mapping[str, object]
    fake_output: Mapping[str, object]


@dataclass(frozen=True)
class GoldenSample:
    id: str
    kind: str
    qualities: tuple[str, ...]
    media: Mapping[str, object] | None
    capabilities: Mapping[Capability, CapabilitySpec]

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities


def _require_str(value: object, *, field: str, sample_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkProvenanceError(
            f"sample {sample_id!r} is missing required non-empty field {field!r}"
        )
    return value


def _load_sample(path: Path) -> GoldenSample:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"golden sample {path.name} is not a JSON object")
    sample_id = _require_str(raw.get("id"), field="id", sample_id=path.stem)
    kind = _require_str(raw.get("kind"), field="kind", sample_id=sample_id)
    qualities_raw = raw.get("qualities", [])
    if not isinstance(qualities_raw, list) or not all(isinstance(q, str) for q in qualities_raw):
        raise ValueError(f"golden sample {sample_id} has a malformed qualities list")
    media = raw.get("media")
    if media is not None and not isinstance(media, dict):
        raise ValueError(f"golden sample {sample_id} has a malformed media block")

    capabilities_raw = raw.get("capabilities", {})
    if not isinstance(capabilities_raw, dict) or not capabilities_raw:
        raise ValueError(f"golden sample {sample_id} declares no capabilities")
    capabilities: dict[Capability, CapabilitySpec] = {}
    for key, spec_raw in capabilities_raw.items():
        capability = Capability(key)
        if not isinstance(spec_raw, dict):
            raise ValueError(f"golden sample {sample_id} capability {key} is malformed")
        ground_truth = spec_raw.get("ground_truth")
        fake_output = spec_raw.get("fake_output")
        if not isinstance(ground_truth, dict) or not isinstance(fake_output, dict):
            raise ValueError(
                f"golden sample {sample_id} capability {key} needs ground_truth and fake_output"
            )
        capabilities[capability] = CapabilitySpec(
            capability=capability,
            # Provenance is mandatory: a sample without a prompt version cannot be scored.
            prompt_version=_require_str(
                spec_raw.get("prompt_version"), field="prompt_version", sample_id=sample_id
            ),
            route_revision=_require_str(
                spec_raw.get("route_revision"), field="route_revision", sample_id=sample_id
            ),
            ground_truth=ground_truth,
            fake_output=fake_output,
        )

    return GoldenSample(
        id=sample_id,
        kind=kind,
        qualities=tuple(qualities_raw),
        media=media,
        capabilities=capabilities,
    )


def load_samples(samples_dir: Path | None = None) -> tuple[GoldenSample, ...]:
    """Return every golden sample sorted by id, so a run order is deterministic."""

    directory = samples_dir or _DEFAULT_SAMPLES_DIR
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no golden samples found under {directory}")
    return tuple(_load_sample(path) for path in paths)
