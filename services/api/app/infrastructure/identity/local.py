"""Signed local identity tokens for development and tests only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.core.config import Settings
from app.modules.identity.domain import VerifiedIdentity


class LocalIdentityTokenError(ValueError):
    """Raised when a local token is malformed, unsigned, or invalid."""


class LocalIdentityVerifier:
    """Verify HMAC-signed local claims without accepting arbitrary headers."""

    _prefix = "local.v1"
    _maximum_token_lifetime_seconds = 60 * 60
    _maximum_clock_skew_seconds = 60

    def __init__(self, settings: Settings) -> None:
        if settings.app_env == "production":
            raise RuntimeError("the local identity adapter is not allowed in production")
        self._key = settings.local_identity_signing_key.get_secret_value().encode("utf-8")

    @classmethod
    def sign_for_testing(
        cls,
        *,
        signing_key: str,
        subject: str,
        email: str,
        display_name: str | None = None,
        expires_at: int | None = None,
    ) -> str:
        now = int(time.time())
        payload = {
            "provider": "local",
            "subject": subject,
            "email": email,
            "display_name": display_name,
            "issued_at": now,
            "expires_at": expires_at if expires_at is not None else now + 300,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        encoded = encoded.rstrip("=")
        signed_value = f"{cls._prefix}.{encoded}".encode()
        signature = hmac.new(signing_key.encode("utf-8"), signed_value, hashlib.sha256).hexdigest()
        return f"{cls._prefix}.{encoded}.{signature}"

    async def verify(self, token: str) -> VerifiedIdentity:
        parts = token.split(".")
        if len(parts) != 4 or ".".join(parts[:2]) != self._prefix:
            raise LocalIdentityTokenError("invalid local token format")
        encoded, supplied_signature = parts[2], parts[3]
        signed_value = f"{self._prefix}.{encoded}".encode()
        expected_signature = hmac.new(self._key, signed_value, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise LocalIdentityTokenError("invalid local token signature")
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            provider = str(payload["provider"])
            subject = str(payload["subject"])
            email = str(payload["email"])
            display_name = payload.get("display_name")
            issued_at = int(payload["issued_at"])
            expires_at = int(payload["expires_at"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LocalIdentityTokenError("invalid local token claims") from error
        now = int(time.time())
        if (
            provider != "local"
            or subject != subject.strip()
            or not subject
            or len(subject) > 255
            or "@" not in email
            or len(email) > 320
            or issued_at > now + self._maximum_clock_skew_seconds
            or expires_at <= now
            or expires_at - issued_at > self._maximum_token_lifetime_seconds
        ):
            raise LocalIdentityTokenError("invalid local token claims")
        if display_name is not None and not isinstance(display_name, str):
            raise LocalIdentityTokenError("invalid local token claims")
        return VerifiedIdentity(
            provider=provider, subject=subject, email=email, display_name=display_name
        )
