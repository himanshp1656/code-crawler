# Code Crawler

A multi-tenant web app that clones Python GitHub repositories, parses them using Python's `ast` module, and builds function-level lineage graphs (function = node, function call = directed edge). Built with FastAPI, Temporal, PostgreSQL (SQLAlchemy ORM), and Alembic.

<!-- CI/CD Test Commit -->

---

## Prerequisites

You need these installed on your machine before starting:

| Tool | What it does | Install (macOS) |
|------|-------------|-----------------|
| **Python 3.11+** | Runs the app | `brew install python@3.11` |
| **PostgreSQL** | Stores all data | `brew install postgresql@17` |
| **Temporal CLI** | Durable workflow engine | `brew install temporal` |
| **Git** | Clones repos to analyze | Pre-installed on macOS |

> **Windows/Linux?** Use your OS package manager instead of `brew`. The rest of the steps are the same.

---

## Step-by-Step Setup

### 1. Clone the repo

```bash
git clone <your-repo-url> code-crawler
cd code-crawler
```

### 2. Create a Python virtual environment and install dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install packages (pick one):

```bash
# Using pip
pip install -r requirements.txt

# OR using uv (faster)
uv pip install -r requirements.txt
```

> **Corporate network / SSL errors?** If you see `invalid peer certificate` errors, you likely need to point to your company's CA cert:
> ```bash
> # For pip
> export REQUESTS_CA_BUNDLE="/path/to/your/ca-cert.pem"
>
> # For uv
> export SSL_CERT_FILE="/path/to/your/ca-cert.pem"
> ```

### 3. Set up PostgreSQL

**Start PostgreSQL** (if not already running):

```bash
brew services start postgresql@17
```

**Create the database user and database:**

```bash
# Create the 'postgres' superuser role (Homebrew doesn't create it by default)
createuser -s postgres

# Set a password for it
psql -U postgres -c "ALTER USER postgres PASSWORD 'postgres';"

# Create the database
createdb -U postgres code_crawler
```

> **Verify it works:**
> ```bash
> psql -U postgres -d code_crawler -c "SELECT 1;"
> ```
> You should see a table with `1` in it. If you get a connection error, make sure PostgreSQL is running (`brew services list`).

**Custom connection string?** Set the `POSTGRES_DSN` env var:

```bash
export POSTGRES_DSN="postgresql://myuser:mypassword@localhost:5432/code_crawler"
```

Default is `postgresql://postgres:postgres@localhost:5432/code_crawler`.

### 4. Run database migrations

This creates all the tables (`tenants`, `users`, `lineage_nodes`, `admin_accounts`) with proper constraints:

```bash
alembic upgrade head
```

You should see output like:

```
INFO  [alembic.runtime.migration] Running upgrade  -> 001, initial schema
INFO  [alembic.runtime.migration] Running upgrade 001 -> 002, add handle constraints to tenants
```

### 5. Start Temporal dev server

Open a **new terminal** (no venv needed):

```bash
temporal server start-dev
```

This starts Temporal at `localhost:7233` with a web UI at `http://localhost:8233`. Leave it running.

### 6. Start the Temporal worker

Open a **new terminal**, activate the venv, and run:

```bash
cd code-crawler
source .venv/bin/activate
python worker.py
```

You should see:

```
INFO:__main__:Starting Temporal worker on task queue code-crawler-task-queue
```

Leave it running.

### 7. Start the FastAPI server

Open a **new terminal**, activate the venv, and run:

```bash
cd code-crawler
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

The app is now live at **http://localhost:8000**.

---

## Using the App

### Sign up

1. Go to http://localhost:8000/signup
2. Pick **Personal** or **Organization**
3. Choose a handle (this becomes your public URL: `localhost:8000/your-handle`)
4. Fill in display name, email, and password
5. Click **Create account** — you'll land on the dashboard

### Crawl a repo

1. On the dashboard, paste a public GitHub repo URL (e.g., `https://github.com/pallets/flask.git`)
2. Pick a branch (default: `main`)
3. Click **Crawl & Build Lineage**
4. Wait for the workflow to complete — it clones the repo, parses all Python files, and builds the lineage graph
5. Click the repo in "Your lineage" to visualize the function call graph

### Public profile

Visit `http://localhost:8000/{your-handle}` to see your public profile with all crawled repos.

### Admin portal

For managing tenants and users directly:

1. Go to http://localhost:8000/admin/login
2. Default credentials: `admin` / `admin123`
3. Create tenants, assign users, view account types

### CLI alternative

If you just want to trigger a crawl without the web UI (Temporal + worker must be running):

```bash
python run_connector.py --repo https://github.com/pallets/flask.git --branch main --tenant your-handle
```

### API

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "github_repo_url": "https://github.com/pallets/flask.git",
    "branch": "main",
    "language": "python",
    "tenant_id": "your-handle"
  }'
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DSN` | `postgresql://postgres:postgres@localhost:5432/code_crawler` | Database connection string |
| `ADMIN_USERNAME` | `admin` | Admin portal login |
| `ADMIN_PASSWORD` | `admin123` | Admin portal password |
| `SESSION_SECRET_KEY` | `dev-change-me` | Secret for signing session cookies (change in production) |
| `DEFAULT_TENANT_ID` | `default` | Tenant seeded on first startup |
| `DEFAULT_TENANT_NAME` | `Default` | Display name for the default tenant |

---

## Project Structure

```
code-crawler/
├── app/
│   ├── main.py                 # FastAPI app factory + lifespan
│   ├── workflow.py             # Temporal workflow (3 sequential activities)
│   ├── activities.py           # Temporal activity implementations
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── base.py             #   DeclarativeBase
│   │   ├── tenant.py           #   Tenant (with account_type)
│   │   ├── user.py             #   User (with bcrypt password)
│   │   ├── lineage_node.py     #   LineageNode (JSONB upstream_ids)
│   │   └── admin_account.py    #   AdminAccount
│   ├── db/                     # Database session management
│   │   └── session.py          #   async engine, get_session, get_session_context
│   ├── repositories/           # Data access layer
│   │   ├── tenant_repo.py      #   Tenant CRUD + handle existence check
│   │   ├── user_repo.py        #   User CRUD + password verification
│   │   ├── lineage_repo.py     #   Bulk lineage insert + queries
│   │   └── admin_repo.py       #   Admin CRUD
│   ├── services/               # Business logic
│   │   └── signup_service.py   #   Handle validation + atomic signup
│   ├── routes/                 # FastAPI routers (registered in order)
│   │   ├── auth.py             #   /login, /logout, /signup, /check-handle
│   │   ├── dashboard.py        #   /dashboard, /crawl, /lineage-ui, /asset
│   │   ├── admin.py            #   /admin/* (manage tenants/users)
│   │   ├── ingest.py           #   POST /ingest (API)
│   │   └── profile.py          #   /{handle} (public profile, registered LAST)
│   ├── python_ast_parser.py    # AST visitor for Python files
│   ├── lineage_builder.py      # Resolves call edges into lineage graph
│   └── repo_crawler.py         # Git clone/refresh with retries
├── alembic/                    # Database migrations
│   ├── env.py                  #   Async Alembic config
│   └── versions/
│       ├── 001_initial_schema.py
│       └── 002_handle_constraints.py
├── templates/                  # Jinja2 HTML templates
│   ├── base.html               #   Shared layout + dark theme CSS
│   ├── signup.html             #   Self-service signup with live handle check
│   ├── login.html              #   User login
│   ├── dashboard.html          #   Crawl repos + view lineage list
│   ├── profile.html            #   Public profile at /{handle}
│   ├── lineage.html            #   Lineage graph visualization
│   ├── asset.html              #   Single function/asset detail
│   ├── admin_login.html        #   Admin login
│   └── admin_dashboard.html    #   Admin tenant/user management
├── worker.py                   # Temporal worker entrypoint
├── run_connector.py            # CLI to trigger workflow
├── alembic.ini                 # Alembic config
├── requirements.txt            # Python dependencies
└── CLAUDE.md                   # AI assistant instructions
```

---

## How It Works

1. **Signup** — User picks a handle (e.g., `acme-corp`), creating a tenant + user in one transaction
2. **Crawl** — User pastes a GitHub URL; FastAPI starts a Temporal workflow
3. **Clone** — `clone_repo_activity` clones the repo via GitPython
4. **Parse** — `parse_repo_activity` walks all `.py` files and extracts functions, classes, imports, and calls using Python's `ast` module
5. **Build lineage** — `build_lineage_activity` resolves call expressions to function IDs and stores the lineage graph in PostgreSQL
6. **Visualize** — The web UI renders an interactive lineage graph showing which functions call which

---

## Troubleshooting

**`psql: error: connection refused`**
PostgreSQL isn't running. Start it:
```bash
brew services start postgresql@17
```

**`alembic: command not found`**
Make sure your venv is active:
```bash
source .venv/bin/activate
```

**`temporal: command not found`**
Install the Temporal CLI:
```bash
brew install temporal
```

**`connection refused` on port 7233**
Temporal server isn't running. Start it in a separate terminal:
```bash
temporal server start-dev
```

**SSL certificate errors when installing packages**
See the SSL note in Step 2 above. Set `REQUESTS_CA_BUNDLE` (pip) or `SSL_CERT_FILE` (uv) to your corporate CA cert path.

---

## Private GitHub Repos

For private repos, you have two options:

- **SSH**: Ensure `git clone git@github.com:org/private-repo.git` works from your machine
- **HTTPS token**: Export `GITHUB_TOKEN` and modify `repo_crawler.py` to build an authenticated URL

---

## Only Python

The parser only supports Python files (via the `ast` module). The `language` parameter exists but rejects anything other than `"python"`.
