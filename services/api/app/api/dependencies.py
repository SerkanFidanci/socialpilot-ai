"""Authentication dependencies that keep routes free of provider logic."""

from __future__ import annotations

from typing import cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemException
from app.infrastructure.database.session import get_session
from app.infrastructure.identity.local import LocalIdentityTokenError, LocalIdentityVerifier
from app.modules.identity.models import User
from app.modules.identity.service import IdentityService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolve a signed local bearer token into an internal user."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ProblemException(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A bearer token is required.",
        )
    verifier = cast(LocalIdentityVerifier, request.app.state.identity_verifier)
    try:
        verified = await verifier.verify(credentials.credentials)
    except LocalIdentityTokenError:
        raise ProblemException(
            status=401,
            code="INVALID_IDENTITY_TOKEN",
            title="Invalid identity token",
            detail="The identity token could not be accepted.",
        ) from None
    try:
        user = await IdentityService(session).resolve(verified)
        await session.commit()
        return user
    except ProblemException:
        await session.rollback()
        raise
