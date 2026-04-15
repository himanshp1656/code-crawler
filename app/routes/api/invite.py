from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.invite_repo import InviteRepository
from app.repositories.user_repo import UserRepository

router = APIRouter()


async def _get_user(request: Request, session: AsyncSession):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


class GenerateInviteRequest(BaseModel):
    max_uses: int = 5
    expires_in_hours: int = 72


class AcceptInviteRequest(BaseModel):
    username: str
    password: str


@router.post("/generate")
async def generate_invite(
    body: GenerateInviteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    invite_repo = InviteRepository(session)

    # Reuse existing valid token if one exists
    existing = await invite_repo.get_valid_for_tenant(user.tenant_id)
    if existing:
        await session.commit()
        return {
            "token": existing.token,
            "max_uses": existing.max_uses,
            "used_count": existing.used_count,
            "expires_at": existing.expires_at.isoformat() if existing.expires_at else None,
        }

    expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    invite = await invite_repo.create(
        tenant_id=user.tenant_id,
        created_by_user_id=user.id,
        max_uses=body.max_uses,
        expires_at=expires_at,
    )
    await session.commit()
    return {
        "token": invite.token,
        "max_uses": invite.max_uses,
        "used_count": invite.used_count,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
    }


@router.get("/{token}")
async def get_invite_info(token: str, session: AsyncSession = Depends(get_session)):
    invite_repo = InviteRepository(session)
    invite = await invite_repo.get_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite link is invalid")
    if not invite_repo.is_valid(invite):
        raise HTTPException(status_code=410, detail="This invite link has expired or reached its limit")
    return {
        "tenant_id": invite.tenant_id,
        "tenant_name": invite.tenant.tenant_name,
        "invited_by": invite.created_by.username if invite.created_by else None,
        "spots_left": invite.max_uses - invite.used_count,
    }


@router.post("/{token}/accept")
async def accept_invite(
    token: str,
    body: AcceptInviteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    invite_repo = InviteRepository(session)
    invite = await invite_repo.get_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite link is invalid")
    if not invite_repo.is_valid(invite):
        raise HTTPException(status_code=410, detail="This invite link has expired or reached its limit")

    user_repo = UserRepository(session)
    existing = await user_repo.get_by_username(body.username.strip())
    if existing:
        raise HTTPException(status_code=422, detail="Username already taken")

    user = await user_repo.create(
        tenant_id=invite.tenant_id,
        username=body.username.strip(),
        password=body.password,
    )
    await invite_repo.increment_used(invite)
    await session.commit()

    request.session["user_id"] = str(user.id)
    return {"user_id": user.id, "username": user.username, "tenant_id": user.tenant_id}
