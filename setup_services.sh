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

# Nginx
sudo tee /etc/nginx/sites-available/code-crawler << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/code-crawler /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo "Nginx configured successfully"

# Temporal UI — password protected
sudo apt install -y apache2-utils
echo "Him@211656032" | sudo htpasswd -i -c /etc/nginx/temporal_htpasswd himanshp1656

sudo tee /etc/nginx/sites-available/temporal << 'EOF'
server {
    listen 8233;
    server_name api.jiofibre.in;

    auth_basic "Temporal UI";
    auth_basic_user_file /etc/nginx/temporal_htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8233;
        proxy_set_header Host $host;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/temporal /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx

echo "Temporal UI configured at http://api.jiofibre.in:8233"
