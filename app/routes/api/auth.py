from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository
from app.services.signup_service import SignupService

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    handle: str
    display_name: str
    username: str
    password: str
    account_type: str = "personal"


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_repo = UserRepository(session)
    user = await user_repo.get_by_username(body.username)
    if not user or not UserRepository.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.session["user_id"] = str(user.id)
    return {"user_id": user.id, "username": user.username, "tenant_id": user.tenant_id}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.post("/signup")
async def signup(
    body: SignupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    svc = SignupService(TenantRepository(session), UserRepository(session))
    try:
        _tenant, user = await svc.signup(
            handle=body.handle.lower().strip(),
            display_name=body.display_name.strip(),
            username=body.username.strip(),
            password=body.password,
            account_type=body.account_type,
        )
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    request.session["user_id"] = str(user.id)
    return {"user_id": user.id, "username": user.username, "tenant_id": user.tenant_id}


@router.get("/me")
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return {"user_id": user.id, "username": user.username, "tenant_id": user.tenant_id}
