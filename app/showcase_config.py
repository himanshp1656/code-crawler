from __future__ import annotations

import os

SHOWCASE_TENANT_ID = os.getenv("SHOWCASE_TENANT_ID", "showcase")
SHOWCASE_TENANT_NAME = os.getenv("SHOWCASE_TENANT_NAME", "Code Crawler Demo")
SHOWCASE_ENABLED = os.getenv("SHOWCASE_ENABLED", "true").lower() in ("1", "true", "yes")

# Suggested repos to crawl into the showcase tenant (see scripts/seed_showcase_repos.sh).
SHOWCASE_SUGGESTED_REPOS = [
    {"url": "https://github.com/pallets/flask.git", "branch": "main"},
    {"url": "https://github.com/tiangolo/fastapi.git", "branch": "master"},
    {"url": "https://github.com/himanshp1656/sample-repo.git", "branch": "main"},
]
