"""Pure presentation of verified values.

A price that reaches a frame has to look like a price, and the string that does so is produced
here — by code, from an integer and a currency code, with no model involved (PRD §2.2, §11.3).
Keeping the formatting in one pure function is what lets a test assert that the drawn text is a
function of the stored record and nothing else.
"""

from __future__ import annotations

from typing import Final

# ISO-4217 minor-unit exponents differ per currency and cannot be guessed from the code. This
# table holds only the currencies the product actually records today; anything else formats
# with two decimals, which is the majority convention and, more importantly, is never silently
# wrong in a way that changes the number — it can only misplace a separator, and an unknown
# currency reaching here is a catalogue problem the brand module already gates.
_MINOR_UNIT_EXPONENT: Final[dict[str, int]] = {
    "TRY": 2,
    "EUR": 2,
    "USD": 2,
    "GBP": 2,
}
_DEFAULT_EXPONENT: Final = 2


def format_money(*, amount_minor: int, currency: str) -> str:
    """Render integer minor units as a display string, e.g. `149,90 TRY`.

    The decimal comma matches the product's Turkish-first audience. The currency code is
    appended rather than a symbol: symbols are ambiguous across locales and the code is what
    the catalogue actually stores.
    """

    code = currency.strip().upper()
    exponent = _MINOR_UNIT_EXPONENT.get(code, _DEFAULT_EXPONENT)
    if exponent == 0:
        return f"{amount_minor} {code}"
    unit, fraction = divmod(abs(amount_minor), 10**exponent)
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{unit},{fraction:0{exponent}d} {code}"
