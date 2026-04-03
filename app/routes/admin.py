from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.admin_repo import AdminRepository
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository

router = APIRouter()
templates = Jinja2Templates(directory="templates")

DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


async def _require_admin(request: Request, session: AsyncSession):
    admin_username = request.session.get("admin_username")
    if not admin_username:
        raise HTTPException(status_code=401, detail="Admin not logged in")
    repo = AdminRepository(session)
    admin = await repo.get_by_username(admin_username)
    if not admin:
        raise HTTPException(status_code=403, detail="Admin not found")
    return admin


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if request.session.get("admin_username"):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@router.post("/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    repo = AdminRepository(session)
    admin = await repo.get_by_username(username)
    if not admin or not AdminRepository.verify_password(
        password, admin.password_hash
    ):
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"error": "Invalid admin credentials"},
            status_code=401,
        )
    request.session["admin_username"] = admin.username
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/logout")
async def admin_logout(request: Request):
    request.session.pop("admin_username", None)
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _require_admin(request, session)
    tenant_repo = TenantRepository(session)
    tenants = await tenant_repo.list_all()

    users_by_tenant: Dict[str, List[Dict[str, Any]]] = {}
    user_repo = UserRepository(session)
    for t in tenants:
        users = await user_repo.list_for_tenant(t.tenant_id)
        users_by_tenant[t.tenant_id] = [
            {
                "username": u.username,
                "is_active": u.is_active,
                "created_at": u.created_at,
            }
            for u in users
        ]

    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {"tenants": tenants, "users_by_tenant": users_by_tenant},
    )


@router.post("/tenants")
async def admin_create_tenant(
    request: Request,
    tenant_id: str = Form(...),
    tenant_name: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    await _require_admin(request, session)
    tenant_repo = TenantRepository(session)
    await tenant_repo.create(tenant_id, tenant_name)
    await session.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/users")
async def admin_create_user(
    request: Request,
    tenant_id: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    await _require_admin(request, session)
    user_repo = UserRepository(session)
    await user_repo.create(tenant_id=tenant_id, username=username, password=password)
    await session.commit()
    return RedirectResponse(url="/admin", status_code=303)
