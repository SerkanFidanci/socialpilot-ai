"""The strict monetary type, and the guarantee that every API money field actually uses it.

Codex found `price_minor: 165.0` accepted with `201` and stored as `165`, while `165.5` was
rejected with `400`. No money was lost, but the failure mode is the expensive kind: a client
computing an amount in floating point works until the one value that does not round cleanly.
The tests below pin both halves of the fix — the type itself, and the fact that no monetary
field on the public contract escapes it.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

import app.api.routes as routes_package
from app.core.money import MAX_MINOR_UNITS, MinorUnits, is_minor_units

# Every JSON shape that is not an integer, one per row, with the reason it must be refused.
REJECTED: list[tuple[Any, str]] = [
    (165.0, "an integral float is still a float — silent coercion is what hid the bug"),
    (165.5, "a fractional amount is not a count of minor units"),
    ("165", "a numeric string is a string; the contract is an integer"),
    (True, "bool subclasses int in Python but is not an amount"),
    (None, "a missing amount is not zero"),
    (MAX_MINOR_UNITS + 1, "beyond the upper bound"),
    (-1, "money in minor units is never negative"),
]

_MINOR = TypeAdapter(MinorUnits)


def test_minor_units_accepts_only_json_integers() -> None:
    assert _MINOR.validate_python(16500) == 16500
    assert _MINOR.validate_python(0) == 0
    assert _MINOR.validate_python(MAX_MINOR_UNITS) == MAX_MINOR_UNITS
    for value, reason in REJECTED:
        with pytest.raises(ValidationError):
            _MINOR.validate_python(value)
            pytest.fail(f"{value!r} was accepted: {reason}")


def test_minor_units_rejects_a_float_arriving_as_json() -> None:
    """The wire format is what matters: `165.0` parsed from a JSON body must also be refused."""

    assert _MINOR.validate_json("16500") == 16500
    for payload in ("165.0", "165.5", '"165"', "true", "null"):
        with pytest.raises(ValidationError):
            _MINOR.validate_json(payload)


def test_is_minor_units_is_the_non_pydantic_half_of_the_same_rule() -> None:
    assert is_minor_units(0) and is_minor_units(MAX_MINOR_UNITS)
    assert not is_minor_units(True)
    assert not is_minor_units(165.0)
    assert not is_minor_units(-1)
    assert not is_minor_units(MAX_MINOR_UNITS + 1)
    assert not is_minor_units("165")


def _monetary_api_fields() -> list[tuple[str, str, Any]]:
    """Every `*_minor` field on every Pydantic model reachable from the HTTP routes."""

    found: list[tuple[str, str, Any]] = []
    for module_info in pkgutil.iter_modules(routes_package.__path__):
        module = importlib.import_module(f"{routes_package.__name__}.{module_info.name}")
        for attribute in vars(module).values():
            if not (isinstance(attribute, type) and issubclass(attribute, BaseModel)):
                continue
            if attribute.__module__ != module.__name__:
                continue
            for name, field in attribute.model_fields.items():
                if name.endswith("_minor"):
                    found.append((attribute.__name__, name, field.rebuild_annotation()))
    return found


def test_every_monetary_field_on_the_api_uses_the_strict_type() -> None:
    """A new money field must inherit the rule; discovery is automatic so it cannot be forgotten.

    The check is behavioural rather than a type identity comparison: a field wrapped in
    `X | None` still has to refuse a float, and that is the property that matters.
    """

    fields = _monetary_api_fields()
    discovered = {(model, name) for model, name, _ in fields}
    assert {
        ("PricePayload", "price_minor"),
        ("ProductResponse", "price_minor"),
        ("CampaignOfferRequest", "discount_amount_minor"),
        ("CampaignOfferResponse", "discount_amount_minor"),
    } <= discovered, discovered

    for model, name, annotation in fields:
        adapter = TypeAdapter(annotation)
        assert adapter.validate_python(16500) == 16500, f"{model}.{name}"
        for value in (165.0, 165.5, "165", True, MAX_MINOR_UNITS + 1, -1):
            with pytest.raises(ValidationError):
                adapter.validate_python(value)
                pytest.fail(f"{model}.{name} accepted {value!r}")
