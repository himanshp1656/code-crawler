#!/usr/bin/env bash
# Crawl demo repos into the public showcase tenant.
# Requires: Temporal dev server + worker running, Postgres migrated.
set -euo pipefail

TENANT="${SHOWCASE_TENANT_ID:-showcase}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "Seeding showcase tenant: ${TENANT}"
echo "Make sure temporal server and worker.py are running."
echo

REPOS=(
  "https://github.com/pallets/flask.git|main"
  "https://github.com/tiangolo/fastapi.git|master"
  "https://github.com/himanshp1656/sample-repo.git|main"
)

for entry in "${REPOS[@]}"; do
  url="${entry%%|*}"
  branch="${entry##*|}"
  echo "→ ${url} (${branch})"
  python run_connector.py --repo "$url" --branch "$branch" --tenant "$TENANT"
  echo
done

echo "Done. Public API: GET /api/showcase"
