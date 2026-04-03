from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository
from app.services.signup_service import SignupService

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", include_in_schema=False)
async def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    user_repo = UserRepository(session)
    user = await user_repo.get_by_username(username)

    if not user or not UserRepository.verify_password(
        password, user.password_hash
    ):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid credentials"}, status_code=401
        )

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ── Signup ──────────────────────────────────────────────────────────────


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "signup.html", {"errors": None})


@router.post("/signup")
async def signup(
    request: Request,
    handle: str = Form(...),
    display_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    account_type: str = Form("personal"),
    session: AsyncSession = Depends(get_session),
):
    svc = SignupService(TenantRepository(session), UserRepository(session))

    try:
        _tenant, user = await svc.signup(
            handle=handle.lower().strip(),
            display_name=display_name.strip(),
            username=username.strip(),
            password=password,
            account_type=account_type,
        )
        await session.commit()
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "errors": str(exc),
                "handle": handle,
                "display_name": display_name,
                "username": username,
                "account_type": account_type,
            },
            status_code=422,
        )

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/check-handle")
async def check_handle(
    handle: str,
    session: AsyncSession = Depends(get_session),
):
    """API for live handle availability check from the signup form."""
    svc = SignupService(TenantRepository(session), UserRepository(session))
    errors = svc.validate_handle(handle.lower().strip())
    if not errors:
        taken = await TenantRepository(session).exists(handle.lower().strip())
        if taken:
            errors.append("Already taken.")
    return {"available": len(errors) == 0, "errors": errors}
