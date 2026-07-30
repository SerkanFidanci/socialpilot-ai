"""The one monetary type: a strict integer count of minor units.

Money is stored, computed and transported as an integer number of minor units (16500 = ₺165,00).
The rule is easy to state and easy to lose at the *edge*: Pydantic's default lax mode accepts a
JSON float whose value happens to be integral and silently coerces it, so ``165.0`` used to pass
while ``165.5`` was rejected. That is the worst possible failure shape — a client computing
``price * 100`` in floating point sends ``16500.0`` most of the time and
``16499.999999999998`` occasionally, so it looks correct until it randomly is not.

``MinorUnits`` closes that: it accepts a JSON integer and nothing else. A float (integral or
not), a numeric string, a bool and ``null`` are all rejected by schema validation, before any
rule runs or any row is touched, with the standard ``REQUEST_VALIDATION_FAILED`` contract. The
negative and upper bounds live here too, so every monetary field in the system — today the brand
catalogue, tomorrow the advertising budget layer — inherits the same limits from one place.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

MAX_MINOR_UNITS = 10**12
"""Upper bound for any monetary amount, in minor units.

10^12 minor units is 10^10 major units — far above any real catalogue price or campaign
discount, and well inside PostgreSQL ``BIGINT``. Its purpose is to reject nonsense (an overflow,
a mis-scaled amount, a hostile 10^30) rather than to model a business limit.
"""

MinorUnits = Annotated[int, Field(strict=True, ge=0, le=MAX_MINOR_UNITS)]
"""A non-negative, bounded integer amount of minor units. JSON floats are rejected, not coerced.

Use this for **every** monetary field on a Pydantic model, request and response alike. Pairing
it with an ISO-4217 currency code is the caller's job; an amount without its currency is not a
price (see ``modules/brands/domain.Money``).
"""


def is_minor_units(value: object) -> bool:
    """True only for a bounded, non-negative Python ``int``.

    ``bool`` is a subclass of ``int`` and is explicitly not an amount, so ``True`` never passes
    for ``1``. This is the non-Pydantic half of the same rule, for domain code that receives an
    amount from somewhere other than an HTTP body.
    """

    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_MINOR_UNITS


__all__ = ["MAX_MINOR_UNITS", "MinorUnits", "is_minor_units"]
