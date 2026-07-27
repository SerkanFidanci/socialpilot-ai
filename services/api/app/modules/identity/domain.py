"""Identity boundary contracts independent from a provider SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Identity claims accepted only after adapter verification."""

    provider: str
    subject: str
    email: str
    display_name: str | None


class IdentityVerifier(Protocol):
    """Verify a bearer token into provider-neutral identity claims."""

    async def verify(self, token: str) -> VerifiedIdentity: ...
