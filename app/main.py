from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from temporalio.client import Client

from app.db import configure as configure_db, get_session_context
from app.repositories.admin_repo import AdminRepository
from app.routes import include_routers

DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    configure_db()
    app.state.temporal_client = await Client.connect("localhost:7233")

    # Seed default admin account (like the old init_db did).
    async with get_session_context() as session:
        admin_repo = AdminRepository(session)
        await admin_repo.upsert(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        await session.commit()

    yield

    # ── Shutdown ──
    await app.state.temporal_client.close()


app = FastAPI(title="Code Crawler", version="0.2.0", lifespan=lifespan)

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]
# Add production Netlify URL if set
_frontend_url = os.getenv("FRONTEND_URL")
if _frontend_url:
    ALLOWED_ORIGINS.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "dev-change-me"),
    session_cookie="codecrawler_session",
    same_site="lax",
)

include_routers(app)
