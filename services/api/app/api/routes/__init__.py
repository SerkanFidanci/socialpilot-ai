"""HTTP route modules and their single registration seam.

`register_routes` is the only place routers are attached to the application. Adding a module
means adding one import and one line here — `app/main.py` does not grow per module, and two
work orders that add a router and change application wiring cannot collide in the same file.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.businesses import router as businesses_router
from app.api.routes.health import router as health_router
from app.api.routes.identity import router as identity_router
from app.api.routes.media import router as media_router


def register_routes(application: FastAPI) -> None:
    """Attach every HTTP router to the application in a stable, documented order."""

    application.include_router(health_router)
    application.include_router(identity_router)
    application.include_router(businesses_router)
    application.include_router(media_router)
