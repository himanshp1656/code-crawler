from fastapi import FastAPI

from .api import api_router
from .ingest import router as ingest_router


def include_routers(app: FastAPI) -> None:
    """Register all routers. Order matters: profile (/{handle}) MUST be last."""
    app.include_router(api_router)   # /api/* — JSON API for React frontend
    app.include_router(ingest_router)
