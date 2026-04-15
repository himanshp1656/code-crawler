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
| Backend server | AWS EC2 | t3.micro, Ubuntu 24.04, ap-south-1 |
| Database | AWS RDS | PostgreSQL, db.t3.micro, 20 GB gp2, ap-south-1 |
| Frontend | Netlify | Free tier, auto-deploy from GitHub |
| SSL | Let's Encrypt | certbot, auto-renew |
| Domain | `api.jiofibre.in` | A record → EC2 public IP |

---

## One-Time EC2 Setup

### 1. Launch EC2

- AMI: Ubuntu 24.04 LTS (x86_64)
- Instance type: t3.micro
- Storage: 30 GB gp3
- Security group inbound rules:
  - SSH (22) — your IP only (or 0.0.0.0/0 if using GitHub Actions deploy)
  - HTTP (80) — 0.0.0.0/0
  - HTTPS (443) — 0.0.0.0/0
  - Custom TCP 8234 — 0.0.0.0/0 (Temporal UI)

### 2. SSH into the instance

```bash
ssh -i ~/Downloads/your-key.pem ubuntu@<EC2_PUBLIC_IP>
```

### 3. Install dependencies

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

### 6. Create .env file

```bash
cat > .env << 'EOF'
POSTGRES_DSN=postgresql+asyncpg://postgres:<password>@<rds-endpoint>:5432/code_crawler
SESSION_SECRET_KEY=<random-secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<your-admin-password>
FRONTEND_URL=https://code-crawler-frontend.netlify.app
EOF
```

### 7. Run database migrations

```bash
set -a && source .env && set +a
alembic upgrade head
```

### 8. Set up systemd services + Nginx

```bash
chmod +x setup_services.sh
./setup_services.sh
```

This creates three systemd services:
- `temporal.service` — Temporal dev server with SQLite persistence
- `code-crawler-worker.service` — Temporal activity worker
- `code-crawler-app.service` — FastAPI via uvicorn on port 8000

And configures Nginx:
- Reverse proxy from port 80 → uvicorn :8000
- Password-protected Temporal UI on port 8234

### 9. Set up SSL with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.jiofibre.in
```

### 10. Update Nginx config for SSL

Copy the contents of `nginx.conf` (in repo root) to `/etc/nginx/sites-available/code-crawler`:

```bash
sudo cp nginx.conf /etc/nginx/sites-available/code-crawler
sudo nginx -t && sudo systemctl reload nginx
```

The config redirects HTTP → HTTPS and proxies all traffic to uvicorn:

```nginx
server {
    listen 80;
    server_name api.jiofibre.in;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name api.jiofibre.in;

    ssl_certificate /etc/letsencrypt/live/api.jiofibre.in/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.jiofibre.in/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

---

## One-Time RDS Setup

1. Create a PostgreSQL RDS instance (db.t3.micro, 20 GB gp2) in ap-south-1
2. Set master username/password
3. Make sure the EC2 security group can reach the RDS security group on port 5432
4. Use the RDS endpoint in `POSTGRES_DSN`

---

## CI/CD — Backend (GitHub Actions)

Every push to `master` on `himanshp1656/code-crawler` automatically deploys to EC2.

**Required GitHub Secrets:**
| Secret | Value |
|--------|-------|
| `EC2_HOST` | EC2 public IP (e.g. `13.206.97.236`) |
| `EC2_SSH_KEY` | Contents of the `.pem` private key |

**Deploy pipeline** (`.github/workflows/deploy.yml`):
1. SSH into EC2
2. `git pull origin master`
3. `pip install -r requirements.txt`
4. Source `.env` then `alembic upgrade head`
5. `sudo systemctl restart code-crawler-worker code-crawler-app`

---

## CI/CD — Frontend (Netlify)

Every push to `main` on `himanshp1656/code-crawler-frontend` triggers a Netlify build.

**Netlify build settings:**
- Build command: `npm run build`
- Publish directory: `dist`

**Required Netlify environment variable:**
| Variable | Value |
|----------|-------|
| `VITE_API_BASE_URL` | `https://api.jiofibre.in` |

**Setup:** Netlify dashboard → Site configuration → Build & deploy → Link repository → connect `himanshp1656/code-crawler-frontend`.

---

## Cross-Domain Session Cookies

The frontend (Netlify) and backend (EC2) are on different domains, so session cookies require:

```python
# app/main.py
SessionMiddleware(
    same_site="none",   # allow cross-site cookies
    https_only=True,    # requires HTTPS on both ends
)
```

This only works when both frontend and backend are on HTTPS.

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

# Run migrations manually
cd /home/ubuntu/code-crawler
source .venv/bin/activate
set -a && source .env && set +a
alembic upgrade head
```

## URLs

| Service | URL |
|---------|-----|
| Frontend | https://code-crawler-frontend.netlify.app |
| Backend API | https://api.jiofibre.in |
| Temporal UI | http://api.jiofibre.in:8234 (basic auth) |
