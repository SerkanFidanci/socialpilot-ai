"""Generate the human-readable endpoint inventory from the OpenAPI contract.

`docs/generated/openapi.json` is ~86 KB and is never read whole by an agent
session. This script renders the same contract as a one-line-per-endpoint table
so callers can answer "what endpoints exist, who may call them, and does this
mutation carry idempotency" for a few hundred tokens instead of ~23k.

Every column is derived from the contract, so the table cannot drift from the
implementation. Run through `make generate-docs`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPOSITORY_ROOT / "docs" / "generated" / "openapi.json"
DESTINATION = REPOSITORY_ROOT / "docs" / "api" / "endpoints.md"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
MUTATING_METHODS = ("post", "put", "patch", "delete")
IDEMPOTENCY_HEADER = "idempotency-key"

TAG_TITLES = {
    "health": "health — canlılık ve bağımlılık hazırlığı",
    "identity": "identity — kimlik",
    "businesses": "businesses — işletme ve üyelik",
    "media": "media — yükleme control-plane'i ve analiz okuması",
}


def _escape(value: str) -> str:
    return value.replace("|", "\\|")


def _purpose(operation: dict[str, Any]) -> str:
    description = (operation.get("description") or "").strip()
    if description:
        return description.splitlines()[0].rstrip(".")
    return (operation.get("summary") or "").strip() or "—"


def _authorization(operation: dict[str, Any], path: str) -> str:
    schemes = sorted(
        name for requirement in operation.get("security") or [] for name in requirement
    )
    if not schemes:
        return "genel (kimlik gerekmez)"
    scope = " + tenant (`business_id`)" if "{business_id}" in path else ""
    return f"`{', '.join(schemes)}`{scope}"


def _idempotency(operation: dict[str, Any], method: str) -> str:
    headers = {
        (parameter.get("name") or "").lower()
        for parameter in operation.get("parameters") or []
        if parameter.get("in") == "header"
    }
    if IDEMPOTENCY_HEADER in headers:
        return "**var** — `Idempotency-Key`"
    if method in MUTATING_METHODS:
        return "yok — **değerlendirilmeli**"
    return "—"


def _success_codes(operation: dict[str, Any]) -> str:
    codes = sorted(code for code in operation.get("responses", {}) if code.startswith("2"))
    return ", ".join(f"`{code}`" for code in codes) or "—"


def render(schema: dict[str, Any]) -> str:
    rows: dict[str, list[tuple[str, str, str, str, str, str]]] = {}
    total = 0
    for path in sorted(schema.get("paths", {})):
        item = schema["paths"][path]
        for method in HTTP_METHODS:
            operation = item.get(method)
            if not operation:
                continue
            total += 1
            tag = (operation.get("tags") or ["other"])[0]
            rows.setdefault(tag, []).append(
                (
                    method.upper(),
                    path,
                    _purpose(operation),
                    _authorization(operation, path),
                    _idempotency(operation, method),
                    _success_codes(operation),
                )
            )

    info = schema.get("info", {})
    lines = [
        "# API Endpoint Envanteri",
        "",
        "<!-- ÜRETİLMİŞ DOSYA — elle düzenlenmez. Kaynak: docs/generated/openapi.json",
        "     Üreten: services/api/scripts/generate_endpoints_doc.py (`make generate-docs`) -->",
        "",
        f"**Kontrat:** {info.get('title', 'API')} `{info.get('version', '?')}`"
        f" · **OpenAPI** `{schema.get('openapi', '?')}`"
        f" · **{total} endpoint**",
        "",
        "> Bu dosya [`../generated/openapi.json`](../generated/openapi.json) yerine okunur:",
        "> aynı kontrat, ~%98 daha az token. Şema/alan detayı gerekiyorsa tek endpoint'i",
        "> `jq '.paths[\"/v1/...\"]'` ile çek — dosyanın tamamını **okuma**.",
        "",
        "Tüm sütunlar kontrattan türetilir; elle yazılmaz, dolayısıyla koddan sapamaz.",
        "**Amaç** sütunu route fonksiyonunun docstring'inden, yoksa özet adından gelir —",
        "boş görünen satırların çözümü ilgili route fonksiyonuna docstring eklemektir.",
        "",
        "## Sütunların anlamı",
        "",
        "| Sütun | Nereden gelir |",
        "|---|---|",
        "| Yetki | Operasyonun `security` şeması; yolda `{business_id}` varsa tenant kapsamı eklenir |",
        "| Idempotency | `Idempotency-Key` header parametresinin varlığı. Mutasyon olup header'ı olmayan endpoint `değerlendirilmeli` işaretlenir — [AGENTS.md](../../AGENTS.md) her dışa görünür mutasyonun idempotency'yi değerlendirmesini ister |",
        "| Başarı | Kontrattaki `2xx` yanıt kodları |",
        "",
        "Hata gövdeleri RFC 9457 Problem Details formatındadır; her operasyon"
        " `400/401/403/404/409/422/500` tanımlar. Bkz."
        " [error-handling.md](../architecture/error-handling.md) ve PRD §30"
        " ([90b-api-error-contracts.md](../product/requirements/90b-api-error-contracts.md)).",
    ]

    for tag in sorted(rows, key=lambda name: (name != "health", name)):
        lines += [
            "",
            f"## {TAG_TITLES.get(tag, tag)}",
            "",
            "| Metot | Yol | Amaç | Yetki | Idempotency | Başarı |",
            "|---|---|---|---|---|---|",
        ]
        for method, path, purpose, auth, idem, codes in rows[tag]:
            lines.append(
                f"| `{method}` | `{_escape(path)}` | {_escape(purpose)} |"
                f" {_escape(auth)} | {idem} | {codes} |"
            )

    lines += [
        "",
        "## Kapsam notu",
        "",
        "Bu envanter yalnızca **uygulanmış** endpoint'leri listeler. PRD §29'un tasarladığı"
        " tam API yüzeyi (içerik, abonelik, bağlantılar, reklam, iş durumu) için"
        " [90b-api-error-contracts.md](../product/requirements/90b-api-error-contracts.md)"
        " okunur; oradaki bir endpoint burada yoksa henüz yazılmamıştır.",
        "",
    ]
    return "\n".join(lines)


def write(schema: dict[str, Any]) -> Path:
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(render(schema), encoding="utf-8", newline="\n")
    return DESTINATION


def main() -> None:
    write(json.loads(CONTRACT.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
