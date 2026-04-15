from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt_pat, encrypt_pat
from app.db import get_session
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


class SavePatRequest(BaseModel):
    pat: str


def _mask_pat(pat: str) -> str:
    """Show only last 4 chars: ghp_****...xxxx"""
    if len(pat) <= 4:
        return "****"
    return f"****...{pat[-4:]}"


async def _validate_github_pat(pat: str) -> str | None:
    """Returns the GitHub username if valid, None if invalid."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"token {pat}"},
            )
            if resp.status_code == 200:
                return resp.json().get("login")
    except Exception:
        pass
    return None


@router.get("")
async def get_connection(request: Request, session: AsyncSession = Depends(get_session)):
    user = await _get_user(request, session)
    if not user.github_pat_encrypted:
        return {"connected": False}
    try:
        pat = decrypt_pat(user.github_pat_encrypted)
        return {"connected": True, "masked": _mask_pat(pat)}
    except Exception:
        return {"connected": False}


@router.post("")
async def save_connection(
    body: SavePatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    pat = body.pat.strip()
    if not pat:
        raise HTTPException(status_code=422, detail="PAT cannot be empty")

    github_user = await _validate_github_pat(pat)
    if not github_user:
        raise HTTPException(status_code=422, detail="Invalid GitHub token — could not authenticate with GitHub")

    user.github_pat_encrypted = encrypt_pat(pat)
    await session.commit()
    return {"connected": True, "masked": _mask_pat(pat), "github_user": github_user}


@router.delete("")
async def remove_connection(request: Request, session: AsyncSession = Depends(get_session)):
    user = await _get_user(request, session)
    user.github_pat_encrypted = None
    await session.commit()
    return {"connected": False}
