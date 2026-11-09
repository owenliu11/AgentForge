#!/usr/bin/env bash
# Run destructive integration tests only against a fresh, disposable database.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"
SUITE="${1:-all}"
case "$SUITE" in
  tests|drills) SUITES=("$SUITE") ;;
  all) SUITES=(tests drills) ;;
  measure) SUITES=() ;;
  *) echo "Usage: scripts/check.sh [tests|drills|all|measure]" >&2; exit 2 ;;
esac
PROJECT="af-checks-$(date +%s)-$$"
COMPOSE=(docker compose -p "$PROJECT" -f "$ROOT/compose.checks.yaml")
ARTIFACTS="$ROOT/reports/checks/$PROJECT"
mkdir -p "$ARTIFACTS"
cleanup() {
  status=$?
  trap - EXIT
  "${COMPOSE[@]}" logs --no-color > "$ARTIFACTS/compose.log" 2>&1 || true
  "${COMPOSE[@]}" down -v --remove-orphans > "$ARTIFACTS/cleanup.log" 2>&1 || {
    echo "Cleanup failed; see $ARTIFACTS/cleanup.log" >&2
    [ "$status" -ne 0 ] || status=1
  }
  echo "Evidence: $ARTIFACTS"
  exit "$status"
}
trap cleanup EXIT
"${COMPOSE[@]}" up -d --wait --wait-timeout 60
ADDRESS="$("${COMPOSE[@]}" port db 5432)"
export DATABASE_URL="postgresql://agentforge:agentforge@127.0.0.1:${ADDRESS##*:}/agentforge_test"
# An inherited cloud setting must not override the disposable database URL.
export DB_HOST=""
export AGENTFORGE_ALLOW_TEST_RESET=1
export DEMO_MODE=false
export MODEL_SCRIPT="$ROOT/tests/fixtures/scripts/repo_analysis.yaml"
export REPOS_ROOT="$ROOT/tests/fixtures"
export SANDBOX_IMAGE="${SANDBOX_IMAGE:-python:3.12-alpine}"
export MOCK_EFFECTS_PUBLIC_URL=""
docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 || docker pull "$SANDBOX_IMAGE"
if [ "$SUITE" = measure ]; then
  export AGENTFORGE_MEASUREMENT_OUTPUT="$ARTIFACTS/recovery.json"
  if [ -n "${AGENTFORGE_PYTHON:-}" ]; then
    "$AGENTFORGE_PYTHON" -m drills.measure_recovery --runs "${MEASURE_RUNS:-20}"
  else
    uv run --frozen python -m drills.measure_recovery --runs "${MEASURE_RUNS:-20}"
  fi
elif [ -n "${AGENTFORGE_PYTHON:-}" ]; then
  "$AGENTFORGE_PYTHON" -m pytest "${SUITES[@]}" --junitxml="$ARTIFACTS/junit.xml" \
    --basetemp="$ARTIFACTS/pytest-tmp"
else
  uv run --frozen pytest "${SUITES[@]}" --junitxml="$ARTIFACTS/junit.xml" \
    --basetemp="$ARTIFACTS/pytest-tmp"
fi
