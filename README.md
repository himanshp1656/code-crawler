## Code Crawler (FastAPI + Temporal)

This is a fresh implementation of your **code-crawler**:

- **FastAPI** HTTP API to trigger ingestion
- **Temporal** workflow + activities for durable ingestion
- **GitPython + AST** to crawl Python code and build lineage

It:

- Clones a Git repo
- Walks all `.py` files
- Parses via Python `ast`
- Extracts functions, classes, imports, calls
- Builds a function-level lineage graph:
  - each function = node
  - each function call = directed edge
- Stores JSON as:

```json
{
  "nodes": [],
  "edges": []
}
```

---

### Project layout

- `requirements.txt` – FastAPI, Temporal, GitPython
- `app/`
  - `main.py` – FastAPI app with `/ingest` endpoint
  - `repo_crawler.py` – clones/refreshes Git repos (retry-safe)
  - `python_ast_parser.py` – AST parsing to an intermediate model
  - `lineage_builder.py` – builds lineage graph and writes JSON
  - `activities.py` – Temporal activities:
    - `clone_repo_activity`
    - `parse_repo_activity`
    - `build_lineage_activity`
  - `workflow.py` – `CodeCrawlerWorkflow` + `TASK_QUEUE`
- `worker.py` – Temporal worker process
- `run_connector.py` – CLI to fire the workflow without HTTP

---

### 1. Install dependencies (Python 3.11+ recommended)

From the project root:

```bash
cd ~/Desktop/code-crawler

python3.11 -m venv .venv          # or python -m venv .venv if 3.11 is default
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

If you see missing packages later (`temporalio`, `git`), re-run:

```bash
pip install -r requirements.txt
```

---

### 1.5 Configure Postgres
The app writes lineage nodes into Postgres and reads them back for the UI.

Set `POSTGRES_DSN` (defaults to `postgresql://postgres:postgres@localhost:5432/code_crawler`):
```bash
export POSTGRES_DSN="postgresql://<user>:<password>@localhost:5432/code_crawler"
```

On startup (FastAPI) and inside the Temporal activity, the required table is created automatically (`lineage_nodes`).

### Tenant setup (multi-company)
The DB is now tenant-aware using a `tenants` table and a `tenant_id` column on `lineage_nodes`.

- Default tenant id: `default` (env `DEFAULT_TENANT_ID` to change)
- Data isolation is done server-side by always using the logged-in user's `tenant_id`.
- For multi-company support you should create tenants in `tenants`, and users in `users` linked to the right `tenant_id`.
- The website endpoints are:
  - `GET /login`
  - `GET /dashboard`
  - `POST /crawl` (triggers Temporal ingestion for the logged-in user's tenant)
  - `GET /lineage-ui`, `GET /asset` (views lineage for the logged-in user's tenant)

#### User table (login)
The `users` table is created automatically on startup with:
- `username` (unique)
- `password_hash` (bcrypt hash)
- `tenant_id` (FK -> `tenants.tenant_id`)

Since you create users manually for now:
1. Ensure the tenant exists in `tenants` (or use the seeded `default` tenant).
2. Insert the bcrypt hash into `users.password_hash`.

Generate a bcrypt hash like this:
```bash
python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt'], deprecated='auto').hash('YOUR_PASSWORD'))"
```

#### Admin portal (create tenants/users easily)
The app also includes an admin UI so you can create `tenants` and login `users` without manually generating bcrypt hashes.

- Admin login page: `GET /admin/login`
- Admin dashboard: `GET /admin`

Default admin credentials (override via env vars):
- `ADMIN_USERNAME` (default: `admin`)
- `ADMIN_PASSWORD` (default: `admin123`)

---

### 2. Start Temporal locally

You need a Temporal server reachable at `localhost:7233`.

In a separate terminal (no venv required, but fine if you use one):

```bash
temporal server start-dev
```

Leave this running.

---

### 3. Start the Temporal worker

In another terminal with your venv activated:

```bash
cd ~/Desktop/code-crawler
source .venv/bin/activate

python worker.py
```

This:

- Connects to Temporal at `localhost:7233`
- Registers:
  - `CodeCrawlerWorkflow`
  - `clone_repo_activity`
  - `parse_repo_activity`
  - `build_lineage_activity`
- Listens on task queue: `code-crawler-task-queue`

Keep this process running.

---

### 4. Run the FastAPI app

In a third terminal (venv active):

```bash
cd ~/Desktop/code-crawler
source .venv/bin/activate

uvicorn app.main:app --reload --port 8000
```

This starts FastAPI at `http://localhost:8000`.

---

### 5. Trigger ingestion via HTTP

Use `curl`, HTTPie, or your browser (Swagger UI).

- **Swagger UI**:

Open:

```text
http://localhost:8000/docs
```

Use the `POST /ingest` endpoint with body:

```json
{
  "github_repo_url": "https://github.com/org/repo.git",
  "branch": "main",
  "language": "python",
  "tenant_id": "default",
  "output_path": null
}
```

- **curl example**:

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "github_repo_url": "https://github.com/pallets/flask.git",
    "branch": "main",
    "language": "python",
    "tenant_id": "default"
  }'
```

Response:

```json
{
  "workflow_id": "code-crawler-default-main-flask.git",
  "run_id": "..."
}
```

The actual lineage building happens in the Temporal workflow, and each function asset is persisted in Postgres.

---

### 6. Trigger ingestion via CLI (optional)

Instead of HTTP, you can run the workflow directly:

```bash
cd ~/Desktop/code-crawler
source .venv/bin/activate

python run_connector.py --repo https://github.com/pallets/flask.git --branch main
```

This prints:

```text
Lineage stored in Postgres for repo/branch.
Assets: <N>
```

Note: `--output` is now effectively ignored; lineage is persisted in Postgres.



---

### 7. Private GitHub repos

This project intentionally does **not** depend on Atlan’s SecretStore. For private repos:

- **SSH approach**: ensure you can `git clone git@github.com:org/private-repo.git` from this machine.re
- **Token over HTTPS**:
  - Export a token in your shell (e.g. `GITHUB_TOKEN`).
  - Update `repo_crawler.clone_repository` to build an authenticated HTTPS URL using that token (we can wire that in next if you want).

The Temporal logic does not need to change—only how we construct the repo URL.

