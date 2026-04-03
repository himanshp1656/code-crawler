import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from temporalio.client import Client
from starlette.middleware.sessions import SessionMiddleware
from passlib.context import CryptContext

from .workflow import CodeCrawlerWorkflow, TASK_QUEUE
from fastapi.templating import Jinja2Templates
from .db import (
    DEFAULT_TENANT_ID,
    fetch_lineage_data,
    get_user_by_id,
    get_admin_by_username,
    get_user_by_username,
    init_db,
    create_tenant,
    create_user_for_tenant,
    list_repo_branches_for_tenant,
    list_tenants,
    list_users_for_tenant,
    normalize_repo_name,
)
templates = Jinja2Templates(directory="templates")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class IngestionRequest(BaseModel):
    github_repo_url: str = Field(..., description="GitHub repository URL")
    branch: str = Field("main", description="Git branch name")
    language: str = Field("python", description="Source language (python only)")
    tenant_id: str = Field(
        DEFAULT_TENANT_ID,
        description="Tenant id/slug (used to isolate data in Postgres)",
    )
    output_path: Optional[str] = Field(
        None, description="Optional custom path for lineage JSON"
    )


class IngestionResponse(BaseModel):
    workflow_id: str
    run_id: str


app = FastAPI(title="Code Crawler", version="0.1.0")

temporal_client: Optional[Client] = None

# Store the logged-in user id in a signed cookie session.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "dev-change-me"),
    session_cookie="codecrawler_session",
    same_site="lax",
)


async def get_authenticated_user(request: Request) -> Dict[str, Any]:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user = await get_user_by_id(int(user_id))
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


async def get_authenticated_admin(request: Request) -> Dict[str, Any]:
    admin_username = request.session.get("admin_username")
    if not admin_username:
        raise HTTPException(status_code=401, detail="Admin not logged in")
    admin = await get_admin_by_username(admin_username)
    if not admin:
        raise HTTPException(status_code=403, detail="Admin not found")
    return admin


@app.on_event("startup")
async def startup_event() -> None:
    global temporal_client
    temporal_client = await Client.connect("localhost:7233")
    # Ensure schema exists before the UI tries to query lineage.
    await init_db()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global temporal_client
    if temporal_client:
        await temporal_client.close()
        temporal_client = None


@app.post("/ingest", response_model=IngestionResponse)
async def start_ingestion(
    req: IngestionRequest,
    user: Dict[str, Any] = Depends(get_authenticated_user),
) -> IngestionResponse:
    """
    Trigger the code-crawler Temporal workflow via HTTP.
    """
    assert temporal_client is not None, "Temporal client not initialized"

    # Never allow a client to write lineage for another tenant.
    if req.tenant_id != user["tenant_id"]:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    handle = await temporal_client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[
            req.github_repo_url,
            req.branch,
            req.language,
            req.output_path,
            user["tenant_id"],
        ],
        id=f"code-crawler-{user['tenant_id']}-{req.branch}-{req.github_repo_url.rsplit('/', 1)[-1]}",
        task_queue=TASK_QUEUE,
    )

    return IngestionResponse(workflow_id=handle.id, run_id=handle.result_run_id)


@app.get("/lineage-data")
async def get_lineage_data(
    repo: str,
    branch: str = "main",
    user: Dict[str, Any] = Depends(get_authenticated_user),
):
    safe_repo = normalize_repo_name(repo)
    data = await fetch_lineage_data(user["tenant_id"], safe_repo, branch)
    return data


@app.get("/lineage-ui", response_class=HTMLResponse)
async def lineage_ui(
    request: Request,
    repo: str,
    branch: str = "main",
    user: Dict[str, Any] = Depends(get_authenticated_user),
):
    return templates.TemplateResponse(
        "lineage.html",
        {
            "request": request,
            "repo": repo,
            "branch": branch,
        },
    )
@app.get("/asset", response_class=HTMLResponse)
async def asset_view(
    request: Request,
    repo: str,
    branch: str,
    asset_id: str,
    user: Dict[str, Any] = Depends(get_authenticated_user),
):
    return templates.TemplateResponse(
        "asset.html",
        {
            "request": request,
            "repo": repo,
            "branch": branch,
            "asset_id": asset_id,
        },
    )


@app.get("/")
async def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = await get_user_by_username(username)
    if not user or not pwd_context.verify(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid credentials"}, status_code=401
        )

    request.session["user_id"] = str(user["id"])
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Dict[str, Any] = Depends(get_authenticated_user),
):
    repo_branches = await list_repo_branches_for_tenant(user["tenant_id"])
    lineages = [
        {
            "repo_q": quote(x["repo"], safe=""),
            "branch_q": quote(x["branch"], safe=""),
            "repo": x["repo"],
            "branch": x["branch"],
        }
        for x in repo_branches
    ]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "tenant_id": user["tenant_id"],
            "lineages": lineages,
        },
    )


@app.post("/crawl")
async def crawl(
    github_repo_url: str = Form(...),
    branch: str = Form("main"),
    user: Dict[str, Any] = Depends(get_authenticated_user),
):
    assert temporal_client is not None, "Temporal client not initialized"

    handle = await temporal_client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[
            github_repo_url,
            branch,
            "python",
            None,
            user["tenant_id"],
        ],
        id=f"code-crawler-{user['tenant_id']}-{branch}-{github_repo_url.rsplit('/', 1)[-1]}",
        task_queue=TASK_QUEUE,
    )
    _ = handle  # We don't block on lineage; UI can refresh when ready.

    return RedirectResponse(
        url=f"/lineage-ui?repo={quote(github_repo_url, safe='')}&branch={quote(branch, safe='')}",
        status_code=303,
    )


# -----------------------
# Admin portal
# -----------------------


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if request.session.get("admin_username"):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        "admin_login.html", {"request": request, "error": None}
    )


@app.post("/admin/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    admin = await get_admin_by_username(username)
    if not admin or not pwd_context.verify(password, admin["password_hash"]):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "Invalid admin credentials"},
            status_code=401,
        )

    request.session["admin_username"] = admin["username"]
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/logout")
async def admin_logout(request: Request):
    request.session.pop("admin_username", None)
    return RedirectResponse(url="/admin/login", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    _: Dict[str, Any] = Depends(get_authenticated_admin),
):
    tenants = await list_tenants()
    # Load users per tenant for display (small local dataset).
    users_by_tenant: Dict[str, List[Dict[str, Any]]] = {}
    for t in tenants:
        users_by_tenant[t["tenant_id"]] = await list_users_for_tenant(t["tenant_id"])

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "tenants": tenants,
            "users_by_tenant": users_by_tenant,
        },
    )


@app.post("/admin/tenants")
async def admin_create_tenant(
    tenant_id: str = Form(...),
    tenant_name: str = Form(...),
    _: Dict[str, Any] = Depends(get_authenticated_admin),
):
    await create_tenant(tenant_id=tenant_id, tenant_name=tenant_name)
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users")
async def admin_create_user(
    tenant_id: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    _: Dict[str, Any] = Depends(get_authenticated_admin),
):
    await create_user_for_tenant(tenant_id=tenant_id, username=username, password=password)
    return RedirectResponse(url="/admin", status_code=303)
