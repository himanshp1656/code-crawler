#!/bin/bash
set -e

sudo tee /etc/systemd/system/temporal.service << 'EOF'
[Unit]
Description=Temporal Dev Server
After=network.target

[Service]
User=ubuntu
ExecStart=/home/ubuntu/.temporalio/bin/temporal server start-dev --db-filename /home/ubuntu/temporal.db
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/code-crawler-worker.service << 'EOF'
[Unit]
Description=Code Crawler Worker
After=temporal.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/code-crawler
EnvironmentFile=/home/ubuntu/code-crawler/.env
ExecStart=/home/ubuntu/code-crawler/.venv/bin/python worker.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/code-crawler-app.service << 'EOF'
[Unit]
Description=Code Crawler App
After=code-crawler-worker.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/code-crawler
EnvironmentFile=/home/ubuntu/code-crawler/.env
ExecStart=/home/ubuntu/code-crawler/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable temporal code-crawler-worker code-crawler-app
sudo systemctl start temporal code-crawler-worker code-crawler-app

echo "All services started successfully"
