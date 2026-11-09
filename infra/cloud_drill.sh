#!/usr/bin/env bash
# The cloud kill drill: stop the live ECS task that owns a run, and check that
# another worker claims generation 2 and finishes with one report applied.
source "$(dirname "$0")/common.sh"

init_backend

IP="$(host_ip)"
CLUSTER="$(tf_output cluster)"
LOG_GROUP="$(tf_output log_group)"
API="http://$IP:8000"
EFFECTS="http://$IP:8001"

run_field() {
  curl -fsS "$API/runs/$RUN" | jq -r "$1"
}

curl -fsS -X POST "$EFFECTS/reset" >/dev/null

# slow_repo's test sleeps for 60 s, so the run is still going when we stop its owner.
RUN="$(curl -fsS -X POST "$API/runs" -H 'content-type: application/json' \
  -d '{"objective": "cloud kill drill", "repo_path": "slow_repo"}' | jq -r .id)"
echo "Run $RUN submitted"

OWNER=""
for _ in $(seq 1 60); do
  OWNER="$(run_field '.lease_owner // empty')"
  [ -n "$OWNER" ] && break
  sleep 1
done
[ -n "$OWNER" ] || { echo "No worker claimed the run" >&2; exit 1; }
echo "Generation 1 is owned by task $OWNER"

sleep 10 # let it reach the slow tests.run step
echo "Stopping task $OWNER"
aws ecs stop-task --cluster "$CLUSTER" --task "$OWNER" --reason "AgentForge kill drill" \
  --query 'task.lastStatus' --output text
STOPPED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

STATUS=""
for _ in $(seq 1 240); do
  STATUS="$(run_field .status)"
  [ "$STATUS" = "COMPLETED" ] || [ "$STATUS" = "FAILED" ] && break
  sleep 1
done

NEW_OWNER="$(run_field .lease_owner)"
GENERATION="$(run_field .lease_generation)"
ERROR="$(run_field '.error // empty')"
CLAIM_AT="$(curl -fsS "$API/runs/$RUN/events" \
  | jq -r '[.[] | select(.type == "run_claimed")][1].created_at // empty')"
STATS="$(curl -fsS "$EFFECTS/stats")"
APPLIED="$(jq -r .reports_applied <<<"$STATS")"

echo
echo "Status:     $STATUS ${ERROR:+($ERROR)}"
echo "New owner:  $NEW_OWNER"
echo "Generation: $GENERATION"
echo "Stopped at: $STOPPED_AT"
echo "Next claim: $CLAIM_AT"
echo "Effects:    $STATS"
echo
echo "CloudWatch lines for this run, from both workers:"
aws logs filter-log-events --log-group-name "$LOG_GROUP" --log-stream-name-prefix worker \
  --filter-pattern "\"$RUN\"" --query 'events[].message' --output text \
  | tr '\t' '\n' | jq -r 'select(.event == "claimed" or .event == "resumed") | "\(.ts) \(.worker_id) generation \(.generation) \(.event)"' \
  || true

if [ "$STATUS" = "COMPLETED" ] && [ "$GENERATION" = "2" ] \
  && [ "$NEW_OWNER" != "$OWNER" ] && [ "$APPLIED" = "1" ]; then
  echo
  echo "Drill passed."
else
  echo
  echo "Drill failed." >&2
  exit 1
fi
