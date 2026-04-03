from __future__ import annotations

import re
from typing import List, Tuple

from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository

HANDLE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,37}[a-z0-9])?$")

RESERVED_HANDLES = frozenset(
    {
        "login",
        "logout",
        "signup",
        "register",
        "dashboard",
        "admin",
        "ingest",
        "api",
        "static",
        "assets",
        "crawl",
        "lineage-ui",
        "lineage-data",
        "asset",
        "settings",
        "profile",
        "help",
        "about",
        "pricing",
        "docs",
        "blog",
        "status",
        "health",
        "new",
        "delete",
        "edit",
        "create",
        "explore",
        "search",
        "www",
        "app",
        "auth",
        "oauth",
        "default",
    }
)


class SignupService:
    def __init__(
        self,
        tenant_repo: TenantRepository,
        user_repo: UserRepository,
    ) -> None:
        self._tenants = tenant_repo
        self._users = user_repo

    def validate_handle(self, handle: str) -> List[str]:
        errors: List[str] = []
        if not HANDLE_RE.match(handle):
            errors.append(
                "Handle must be 3-39 characters, lowercase alphanumeric "
                "and hyphens only, cannot start or end with a hyphen."
            )
        if handle in RESERVED_HANDLES:
            errors.append(f"'{handle}' is reserved and cannot be used.")
        return errors

    async def signup(
        self,
        *,
        handle: str,
        display_name: str,
        username: str,
        password: str,
        account_type: str = "personal",
    ) -> Tuple[Tenant, User]:
        """Create a tenant + first user atomically. Caller must commit."""
        errors = self.validate_handle(handle)
        if errors:
            raise ValueError("; ".join(errors))

        if await self._tenants.exists(handle):
            raise ValueError(f"'{handle}' is already taken.")

        existing_user = await self._users.get_by_username(username)
        if existing_user:
            raise ValueError(f"Username '{username}' is already taken.")

        tenant = await self._tenants.create(handle, display_name, account_type)
        user = await self._users.create(
            tenant_id=handle,
            username=username,
            password=password,
        )
        return tenant, user
