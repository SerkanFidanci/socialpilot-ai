"""Generate the deterministic public OpenAPI contract artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPOSITORY_ROOT / "services" / "api"
sys.path.insert(0, str(API_ROOT))

from app.main import create_app  # noqa: E402


def main() -> None:
    schema = create_app().openapi()
    destination = REPOSITORY_ROOT / "docs" / "generated" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
