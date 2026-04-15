from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.admin_repo import AdminRepository
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository

router = APIRouter()


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class CreateTenantRequest(BaseModel):
    tenant_id: str
    tenant_name: str


class CreateUserRequest(BaseModel):
    tenant_id: str
    username: str
    password: str


async def _require_admin(request: Request, session: AsyncSession):
    admin_username = request.session.get("admin_username")
    if not admin_username:
        raise HTTPException(status_code=401, detail="Admin not logged in")
    repo = AdminRepository(session)
    admin = await repo.get_by_username(admin_username)
    if not admin:
        raise HTTPException(status_code=403, detail="Admin not found")
    return admin


@router.post("/login")
async def admin_login(
    body: AdminLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    repo = AdminRepository(session)
    admin = await repo.get_by_username(body.username)
    if not admin or not AdminRepository.verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    request.session["admin_username"] = admin.username
    return {"username": admin.username}


@router.post("/logout")
async def admin_logout(request: Request):
    request.session.pop("admin_username", None)
    return {"ok": True}


@router.get("/me")
async def admin_me(request: Request, session: AsyncSession = Depends(get_session)):
    admin = await _require_admin(request, session)
    return {"username": admin.username}


@router.get("/tenants")
async def list_tenants(request: Request, session: AsyncSession = Depends(get_session)):
    await _require_admin(request, session)
    tenant_repo = TenantRepository(session)
    tenants = await tenant_repo.list_all()
    user_repo = UserRepository(session)
    result = []
    for t in tenants:
        users = await user_repo.list_for_tenant(t.tenant_id)
        result.append({
            "tenant_id": t.tenant_id,
            "display_name": t.tenant_name,
            "account_type": t.account_type,
            "users": [
                {"id": u.id, "username": u.username, "is_active": u.is_active}
                for u in users
            ],
        })
    return {"tenants": result}


@router.post("/tenants")
async def create_tenant(
    body: CreateTenantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _require_admin(request, session)
    tenant_repo = TenantRepository(session)
    await tenant_repo.create(body.tenant_id, body.tenant_name)
    await session.commit()
    return {"ok": True}


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _require_admin(request, session)
    tenant_repo = TenantRepository(session)
    tenant = await tenant_repo.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await session.delete(tenant)
    await session.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _require_admin(request, session)
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await session.delete(user)
    await session.commit()
    return {"ok": True}


@router.post("/users")
async def create_user(
    body: CreateUserRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _require_admin(request, session)
    user_repo = UserRepository(session)
    await user_repo.create(
        tenant_id=body.tenant_id,
        username=body.username,
        password=body.password,
    )
    await session.commit()
    return {"ok": True}
