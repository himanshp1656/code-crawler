from fastapi import APIRouter

from .admin import router as admin_router
from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .invite import router as invite_router
from .profile import router as profile_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
api_router.include_router(invite_router, prefix="/invite", tags=["invite"])
api_router.include_router(dashboard_router, tags=["dashboard"])
api_router.include_router(profile_router, tags=["profile"])
