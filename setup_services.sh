#!/bin/bash
set -e

# Detect original user if run with sudo, fallback to current user
CURRENT_USER=${SUDO_USER:-$(whoami)}
USER_HOME=$(eval echo ~$CURRENT_USER)
PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DOMAIN_NAME=${DOMAIN_NAME:-api.jiofibre.in}

echo "Setting up services for user: $CURRENT_USER"
echo "Home directory: $USER_HOME"
echo "Project directory: $PROJECT_DIR"
echo "Domain name: $DOMAIN_NAME"

sudo tee /etc/systemd/system/temporal.service << EOF
[Unit]
Description=Temporal Dev Server
After=network.target

[Service]
User=$CURRENT_USER
ExecStart=$USER_HOME/.temporalio/bin/temporal server start-dev --db-filename $USER_HOME/temporal.db
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/code-crawler-worker.service << EOF
[Unit]
Description=Code Crawler Worker
After=temporal.service

[Service]
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/python worker.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/code-crawler-app.service << EOF
[Unit]
Description=Code Crawler App
After=code-crawler-worker.service

[Service]
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable temporal code-crawler-worker code-crawler-app
sudo systemctl start temporal code-crawler-worker code-crawler-app

echo "All services started successfully"

# Nginx setup for app (port 80 proxying to 8000)
sudo tee /etc/nginx/sites-available/code-crawler << EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/code-crawler /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo "Nginx configured successfully"

# Temporal UI — password protected
sudo apt install -y apache2-utils
if [ -z "$TEMPORAL_UI_PASSWORD" ] || [ -z "$TEMPORAL_UI_USER" ]; then
  echo "ERROR: TEMPORAL_UI_USER and TEMPORAL_UI_PASSWORD must be set"
  exit 1
fi
echo "$TEMPORAL_UI_PASSWORD" | sudo htpasswd -i -c /etc/nginx/temporal_htpasswd "$TEMPORAL_UI_USER"

sudo tee /etc/nginx/sites-available/temporal << EOF
server {
    listen 8233;
    server_name $DOMAIN_NAME;

    auth_basic "Temporal UI";
    auth_basic_user_file /etc/nginx/temporal_htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8233;
        proxy_set_header Host \$host;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/temporal /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx

echo "Temporal UI configured at http://$DOMAIN_NAME:8233"

