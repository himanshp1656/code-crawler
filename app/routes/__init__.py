from fastapi import FastAPI

from .admin import router as admin_router
from .api import api_router
from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .ingest import router as ingest_router
from .profile import router as profile_router


def include_routers(app: FastAPI) -> None:
    """Register all routers. Order matters: profile (/{handle}) MUST be last."""
    app.include_router(api_router)          # /api/* — JSON API for React frontend
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(admin_router, prefix="/admin")
    app.include_router(ingest_router)
    app.include_router(profile_router)  # catch-all — LAST
