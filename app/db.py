import os
from typing import Any, Dict, List, Optional, Sequence

import asyncpg
from passlib.context import CryptContext

DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@localhost:5432/code_crawler"
DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "default")
DEFAULT_TENANT_NAME = os.getenv("DEFAULT_TENANT_NAME", DEFAULT_TENANT_ID)

DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_pool: Optional[asyncpg.Pool] = None


def normalize_repo_name(repo_url_or_name: str) -> str:
    """
    Convert an input like:
      - https://github.com/org/repo.git -> repo
      - repo -> repo
    to a stable name used for storage and API lookups.
    """
    s = repo_url_or_name.rstrip("/")
    s = s.rsplit("/", 1)[-1]
    return s.replace(".git", "")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)
        
        # This is the magic part
        async def setup_json_codec(conn):
            await conn.set_type_codec(
                'jsonb',
                schema='pg_catalog',
                encoder=json.dumps,
                decoder=json.loads
            )

        _pool = await asyncpg.create_pool(
            dsn=dsn, 
            min_size=1, 
            max_size=5,
            init=setup_json_codec  # Pass the setup function here
        )
    return _pool


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                tenant_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS lineage_nodes (
                asset_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                repo TEXT NOT NULL,
                branch TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                lineno INTEGER NOT NULL,
                upstream_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS lineage_nodes_tenant_repo_branch_idx
            ON lineage_nodes (tenant_id, repo, branch);

            CREATE TABLE IF NOT EXISTS admin_accounts (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        # Seed the initial tenant (useful for local dev + your single `code-crawler.io` start).
        await conn.execute(
            """
            INSERT INTO tenants (tenant_id, tenant_name)
            VALUES ($1, $2)
            ON CONFLICT (tenant_id) DO NOTHING
            """,
            DEFAULT_TENANT_ID,
            DEFAULT_TENANT_NAME,
        )

        # Seed a default admin account for the admin portal.
        # You can override via ADMIN_USERNAME + ADMIN_PASSWORD env vars.
        admin_username = DEFAULT_ADMIN_USERNAME
        admin_password_hash = pwd_context.hash(DEFAULT_ADMIN_PASSWORD[:72])
        await conn.execute(
            """
            INSERT INTO admin_accounts (username, password_hash)
            VALUES ($1, $2)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = EXCLUDED.password_hash
            """,
            admin_username,
            admin_password_hash,
        )


async def get_admin_by_username(username: str) -> Optional[Dict[str, Any]]:
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, password_hash
            FROM admin_accounts
            WHERE username=$1
            """,
            username,
        )
    return dict(row) if row else None


async def list_tenants() -> List[Dict[str, Any]]:
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tenant_id, tenant_name, created_at
            FROM tenants
            ORDER BY created_at DESC
            """
        )
    return [dict(r) for r in rows]


async def create_tenant(tenant_id: str, tenant_name: str) -> None:
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tenants (tenant_id, tenant_name)
            VALUES ($1, $2)
            ON CONFLICT (tenant_id) DO NOTHING
            """,
            tenant_id,
            tenant_name,
        )


async def create_user_for_tenant(
    *,
    tenant_id: str,
    username: str,
    password: str,
) -> None:
    await init_db()
    pool = await get_pool()
    password_hash = pwd_context.hash(password)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (tenant_id, username, password_hash)
            VALUES ($1, $2, $3)
            ON CONFLICT (username) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                password_hash = EXCLUDED.password_hash,
                is_active = TRUE
            """,
            tenant_id,
            username,
            password_hash,
        )


async def list_users_for_tenant(tenant_id: str) -> List[Dict[str, Any]]:
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, username, is_active, created_at
            FROM users
            WHERE tenant_id=$1
            ORDER BY created_at DESC
            """,
            tenant_id,
        )
    return [dict(r) for r in rows]

import json
async def replace_lineage_for_repo_branch(
    *,
    tenant_id: str,
    repo: str,
    branch: str,
    workflow_id: str,
    run_id: str,
    lineage_assets: Sequence[Dict[str, Any]],
) -> int:
    """
    Replace nodes for a (tenant_id, repo, branch) pair with the latest lineage produced by one run.
    Returns number of nodes inserted.
    """
    await init_db()
    pool = await get_pool()

    records: List[tuple] = []
    for asset in lineage_assets:
        asset_id = asset["id"]
        upstream_ids = asset.get("upstream_ids") or []
        records.append(
            (
                asset_id,
                tenant_id,
                repo,
                branch,
                workflow_id,
                run_id,
                asset["name"],
                asset["file"],
                int(asset["lineno"]),
                upstream_ids,
            )
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM lineage_nodes
                WHERE tenant_id=$1 AND repo=$2 AND branch=$3
                """,
                tenant_id,
                repo,
                branch,
            )
            await conn.executemany(
                """
                INSERT INTO lineage_nodes(
                    asset_id, tenant_id, repo, branch, workflow_id, run_id, name,
                    file_path, lineno, upstream_ids
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (asset_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    workflow_id = EXCLUDED.workflow_id,
                    run_id = EXCLUDED.run_id,
                    name = EXCLUDED.name,
                    file_path = EXCLUDED.file_path,
                    lineno = EXCLUDED.lineno,
                    upstream_ids = EXCLUDED.upstream_ids,
                    created_at = now()
                """,
                records,
            )
        return len(records)


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id, username, password_hash, is_active
            FROM users
            WHERE username=$1
            """,
            username,
        )
    return dict(row) if row else None


async def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id, username, is_active
            FROM users
            WHERE id=$1
            """,
            user_id,
        )
    return dict(row) if row else None


async def list_repo_branches_for_tenant(
    tenant_id: str,
) -> List[Dict[str, Any]]:
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT repo, branch
            FROM lineage_nodes
            WHERE tenant_id=$1
            ORDER BY repo, branch
            """,
            tenant_id,
        )
    return [dict(r) for r in rows]


async def fetch_lineage_data(tenant_id: str, repo: str, branch: str) -> Dict[str, Any]:
    await init_db()
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                asset_id,
                name,
                file_path,
                lineno,
                upstream_ids
            FROM lineage_nodes
            WHERE tenant_id=$1 AND repo=$2 AND branch=$3
            ORDER BY name
            """,
            tenant_id,
            repo,
            branch,
        )

    nodes: List[Dict[str, Any]] = []
    for r in rows:
        upstream_ids = r["upstream_ids"] or []
        nodes.append(
            {
                "id": r["asset_id"],
                "name": r["name"],
                "file": r["file_path"],
                "lineno": r["lineno"],
                "upstream_ids": upstream_ids,
            }
        )

    # Build edges on the fly; UI only needs upstream_ids for layout,
    # but we keep `edges` for compatibility with the existing endpoint.
    edges: List[Dict[str, str]] = []
    for n in nodes:
        for upstream in n.get("upstream_ids", []) or []:
            edges.append({"source": upstream, "target": n["id"]})

    return {"nodes": nodes, "edges": edges}

