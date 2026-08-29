# Deployment Guide

## Architecture Overview

```
Browser
  │
  ├── https://code-crawler-frontend.netlify.app  (React SPA)
  │       │
  │       └── API calls ──► https://api.jiofibre.in  (Nginx, port 443)
  │                                │
  │                                └── proxy_pass ──► uvicorn :8000 (FastAPI)
  │                                                        │
  │                                          ┌─────────────┴─────────────┐
  │                                     Temporal Worker            RDS PostgreSQL
  │                                     (python worker.py)         (ap-south-1)
  │                                          │
  │                                    Temporal Dev Server
  │                                    (localhost:7233)
  │
  └── http://api.jiofibre.in:8234  (Temporal UI — basic auth protected)
```

### Services on EC2

| Service | How it runs | Listens on |
|---------|-------------|------------|
| Temporal dev server | systemd: `temporal.service` | `localhost:7233` (gRPC), `localhost:8233` (UI) |
| Temporal worker | systemd: `code-crawler-worker.service` | — (polls Temporal) |
| FastAPI app | systemd: `code-crawler-app.service` | `localhost:8000` |
| Nginx | system | `:80` (redirect), `:443` (HTTPS proxy), `:8234` (Temporal UI) |

---

## Infrastructure

| Component | Service | Spec |
|-----------|---------|------|
| Backend server | VPS Hosting (e.g., DigitalOcean, Hetzner, Linode) | 1-2 vCPU, 1-2 GB RAM, Ubuntu 24.04 |
| Database | PostgreSQL | Local VPS instance OR external managed service (e.g. Vercel Postgres, Supabase, Neon) |
| Frontend | Netlify | Free tier, auto-deploy from GitHub |
| SSL | Let's Encrypt | certbot, auto-renew |
| Domain | `api.jiofibre.in` | A record → VPS public IP |

---

## One-Time VPS Setup

### 1. Launch VPS
- OS: Ubuntu 24.04 LTS
- Firewall / Ports open:
  - SSH (22)
  - HTTP (80)
  - HTTPS (443)
  - Custom TCP 8233 / 8234 (Temporal UI)

### 2. SSH into the instance
```bash
ssh <username>@<VPS_PUBLIC_IP>
```

### 3. Install system dependencies
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git nginx python3 python3.12-venv python3-pip
```

### 4. Install Temporal CLI
```bash
curl -sSf https://temporal.download/cli.sh | sh
# binary lands at ~/.temporalio/bin/temporal
```

### 5. Clone repo and set up Python
```bash
git clone https://github.com/himanshp1656/code-crawler.git
cd code-crawler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 6. Set up the Database (PostgreSQL)

Choose **Option A** (local hosting on the VPS) or **Option B** (external managed db).

#### Option A: Local PostgreSQL Setup
If hosting Postgres directly on the VPS:
```bash
# 1. Install Postgres
sudo apt install -y postgresql postgresql-contrib

# 2. Start and enable service
sudo systemctl enable postgresql --now

# 3. Create database and user (change 'secure_password' to something strong)
sudo -u postgres psql -c "CREATE DATABASE code_crawler;"
sudo -u postgres psql -c "CREATE USER postgres WITH PASSWORD 'secure_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE code_crawler TO postgres;"
```
Your `POSTGRES_DSN` will be:
`postgresql+asyncpg://postgres:secure_password@localhost:5432/code_crawler`

#### Option B: External PostgreSQL Setup (Vercel, Supabase, Neon)
1. Create a PostgreSQL database on your chosen provider.
2. Note the database connection URL (ensure it uses `postgresql+asyncpg://` protocol for SQLAlchemy).

### 7. Create .env file
```bash
cat > .env << 'EOF'
POSTGRES_DSN=postgresql+asyncpg://<username>:<password>@<host>:5432/<database>
SESSION_SECRET_KEY=<random-secret>
ADMIN_USERNAME=<your-admin-username>
ADMIN_PASSWORD=<your-admin-password>
FRONTEND_URL=https://code-crawler-frontend.netlify.app
PAT_SECRET_KEY=<your-secret-pat-key>
TEMPORAL_UI_USER=<username-for-temporal-ui>
TEMPORAL_UI_PASSWORD=<password-for-temporal-ui>
EOF
```

### 8. Run database migrations
```bash
set -a && source .env && set +a
alembic upgrade head
```

### 9. Set up systemd services + Nginx
```bash
chmod +x setup_services.sh
# You can optionally set DOMAIN_NAME before running:
# export DOMAIN_NAME=api.yourdomain.com
./setup_services.sh
```

This dynamically detects your logged-in username and project path, creating three systemd services:
- `temporal.service` — Temporal dev server with SQLite persistence
- `code-crawler-worker.service` — Temporal activity worker
- `code-crawler-app.service` — FastAPI via uvicorn on port 8000

And configures Nginx:
- Reverse proxy from port 80 → uvicorn :8000
- Password-protected Temporal UI on port 8233

### 10. Set up SSL with Let's Encrypt
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.jiofibre.in
```

### 11. Update Nginx config for SSL
Copy the contents of `nginx.conf` (in repo root) to `/etc/nginx/sites-available/code-crawler`:
```bash
sudo cp nginx.conf /etc/nginx/sites-available/code-crawler
sudo nginx -t && sudo systemctl reload nginx
```

---

## CI/CD — Backend (GitHub Actions)

Every push to `master` on `himanshp1656/code-crawler` automatically deploys to the VPS.

**Required GitHub Secrets / Variables:**
| Secret / Variable | Description | Default |
|-------------------|-------------|---------|
| `EC2_HOST` | VPS public IP | |
| `EC2_SSH_KEY` | Contents of the SSH private key | |
| `VPS_USERNAME` (Optional Var) | SSH login username | `ubuntu` |
| `VPS_PROJECT_PATH` (Optional Var) | Path to repository on VPS | `/home/ubuntu/code-crawler` |

---

## CI/CD — Frontend (Netlify)

Every push to `main` on `himanshp1656/code-crawler-frontend` triggers a Netlify build.

**Required Netlify environment variable:**
| Variable | Value |
|----------|-------|
| `VITE_API_BASE_URL` | `https://api.jiofibre.in` |

---

## Cross-Domain Session Cookies
The frontend (Netlify) and backend (VPS) are on different domains, so session cookies require `same_site="none"` and `https_only=True` in `app/main.py`. This requires HTTPS to be set up on both sides.

---

## Useful Commands

```bash
# Check service status
sudo systemctl status temporal code-crawler-worker code-crawler-app

# View logs
sudo journalctl -u code-crawler-app -f
sudo journalctl -u code-crawler-worker -f

# Restart all services
sudo systemctl restart temporal code-crawler-worker code-crawler-app
```

## URLs

| Service | URL |
|---------|-----|
| Frontend | https://code-crawler-frontend.netlify.app |
| Backend API | https://api.jiofibre.in |
| Temporal UI | http://api.jiofibre.in:8233 (basic auth protected) |
