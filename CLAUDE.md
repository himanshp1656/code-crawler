# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Code Crawler is a multi-tenant web application that clones Python GitHub repositories, parses them with Python's `ast` module, and builds function-level lineage graphs (nodes = functions, edges = call relationships). It uses FastAPI for HTTP, Temporal for durable workflow orchestration, PostgreSQL via SQLAlchemy async ORM for storage, and Alembic for schema migrations.

## Running the Application

Requires three processes running simultaneously plus a Postgres database:

```bash
# 1. Setup
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run Alembic migrations
alembic upgrade head

# 3. Temporal dev server (separate terminal)
temporal server start-dev

# 4. Temporal worker (separate terminal, venv active)
python worker.py

# 5. FastAPI server (separate terminal, venv active)
uvicorn app.main:app --reload --port 8000
```

**CLI alternative** (bypasses HTTP, still needs Temporal + worker running):
```bash
python run_connector.py --repo https://github.com/org/repo.git --branch main --tenant default
```

## Key Environment Variables

- `POSTGRES_DSN` — defaults to `postgresql://postgres:postgres@localhost:5432/code_crawler`
- `DEFAULT_TENANT_ID` / `DEFAULT_TENANT_NAME` — tenant seeded on startup (default: `default`)
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — admin portal credentials (default: `admin` / `admin123`)
- `SESSION_SECRET_KEY` — session cookie signing key

## Architecture

### Temporal Workflow Pipeline

`CodeCrawlerWorkflow` (in `app/workflow.py`) runs three sequential activities on task queue `code-crawler-task-queue`:

1. **`clone_repo_activity`** — clones/refreshes repo via GitPython into a temp directory
2. **`parse_repo_activity`** — walks all `.py` files, runs AST parsing, writes intermediate JSON to `output/` (avoids gRPC message size limits)
3. **`build_lineage_activity`** — reads parsed JSON, resolves call edges, stores lineage nodes in Postgres

Activities pass state via a `workflow_args` dict that accumulates keys across steps (`repo_path` → `parsed_repo_path` → `lineage_stats`).

### Layered Architecture

```
app/
  models/          # SQLAlchemy ORM models (Base, Tenant, User, LineageNode, AdminAccount)
  db/              # Async engine + session (configure, get_session, get_session_context)
  repositories/    # Data access layer (TenantRepo, UserRepo, LineageRepo, AdminRepo)
  services/        # Business logic (SignupService with handle validation + reserved names)
  routes/          # FastAPI routers: auth, dashboard, admin, ingest, profile (registered in order)
```

**Two session patterns:**
- `get_session()` — async generator for FastAPI `Depends()`
- `get_session_context()` — async context manager for Temporal activities (no DI available)

### Core Modules

- **`app/python_ast_parser.py`** — `_AstVisitor` extracts functions, classes, imports, and calls into dataclasses (`FunctionDefInfo`, `ClassDefInfo`, `ImportInfo`, `CallInfo`, `FileParseResult`)
- **`app/lineage_builder.py`** — resolves call expressions to function IDs using import alias maps and name indexes; produces `{id, name, file, lineno, upstream_ids}` assets
- **`app/repo_crawler.py`** — retry-safe clone/refresh of git repos

### Multi-Tenancy

All lineage data is scoped by `tenant_id`. The `tenant_id` doubles as the user's URL handle (GitHub-style: `/{handle}`). Supports `personal` and `organization` account types. Data isolation is server-side via query filters, not schema-level.

### Signup & Handles

Self-service signup at `/signup` creates a tenant + user atomically. The handle (tenant_id) is the public URL slug validated with `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`, 3-39 chars, checked against ~40 reserved names. The `/{handle}` route is registered LAST to avoid shadowing explicit routes.

### Web UI

Jinja2 templates in `templates/` using template inheritance from `base.html`. Three portals:
- **User portal**: `/signup` or `/login` → `/dashboard` → `/crawl` (triggers workflow) → `/lineage-ui` + `/asset` (visualize lineage)
- **Public profile**: `/{handle}` — shows tenant display name, account type badge, and crawled repos
- **Admin portal**: `/admin/login` → `/admin` (manage tenants and users)

All templates extend `base.html` which provides CSS variables, shared styles, and consistent dark theme. Page-specific styles go in `{% block extra_style %}`.

Authentication uses signed cookie sessions (`SessionMiddleware`) with bcrypt password hashing.

## Only Python Is Supported

The parser only handles Python (`ast` module). The `language` parameter exists but rejects anything other than `"python"`.
