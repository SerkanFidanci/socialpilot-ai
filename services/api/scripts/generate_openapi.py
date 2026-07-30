"""Generate the deterministic public OpenAPI contract artifact.

Also renders the readable endpoint inventory from the same in-memory schema, so
`docs/api/endpoints.md` cannot drift from `docs/generated/openapi.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPOSITORY_ROOT / "services" / "api"
sys.path.insert(0, str(API_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Resolved at runtime through the sys.path insert above; mypy is configured to ignore the
# missing import for this module in pyproject (ADR-009), so no inline ignore is needed.
from generate_endpoints_doc import write as write_endpoints_doc  # noqa: E402

from app.main import create_app  # noqa: E402


def main() -> None:
    schema = create_app().openapi()
    destination = REPOSITORY_ROOT / "docs" / "generated" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_endpoints_doc(schema)


if __name__ == "__main__":
    main()
