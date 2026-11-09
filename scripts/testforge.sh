#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"
export AGENTFORGE_REPOS_ROOT="$ROOT/tests/fixtures"
PROJECT="${TF_PROJECT:-agentforge-testforge}"
# Only this dedicated namespace can be torn down by this helper.
case "$PROJECT" in
  agentforge-testforge|agentforge-testforge-*) ;;
  *) echo "TF_PROJECT must be agentforge-testforge or start with agentforge-testforge-" >&2; exit 2 ;;
esac
COMPOSE=(docker compose -p "$PROJECT" -f "$ROOT/compose.testforge.yaml")
ARTIFACTS="$ROOT/reports/testforge/$PROJECT"
case "${1:-}" in
  up)
    mkdir -p "$ARTIFACTS"
    IMAGE="${SANDBOX_IMAGE:-python:3.12-alpine}"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
    "${COMPOSE[@]}" up -d --build --wait --wait-timeout 120
    ;;
  smoke)
    mkdir -p "$ARTIFACTS"
    export API_BASE_URL="http://127.0.0.1:${TF_API_PORT:-18010}"
    export EFFECTS_BASE_URL="http://127.0.0.1:${TF_EFFECTS_PORT:-18011}"
    export SMOKE_REPORT="$ARTIFACTS/smoke.json"
    status=0
    if [ -n "${AGENTFORGE_PYTHON:-}" ]; then
      "$AGENTFORGE_PYTHON" scripts/testforge_smoke.py || status=$?
    else
      uv run --frozen python scripts/testforge_smoke.py || status=$?
    fi
    "${COMPOSE[@]}" logs --no-color > "$ARTIFACTS/compose.log" 2>&1 || true
    exit "$status"
    ;;
  logs)
    mkdir -p "$ARTIFACTS"
    "${COMPOSE[@]}" logs --no-color > "$ARTIFACTS/compose.log"
    echo "$ARTIFACTS/compose.log"
    ;;
  down)
    mkdir -p "$ARTIFACTS"
    "${COMPOSE[@]}" logs --no-color > "$ARTIFACTS/compose.log" 2>&1 || true
    "${COMPOSE[@]}" down -v --remove-orphans
    ;;
  *) echo "Usage: scripts/testforge.sh {up|smoke|logs|down}" >&2; exit 2 ;;
esac
