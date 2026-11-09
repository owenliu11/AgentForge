# AgentForge target contract for TestForge

AgentForge is ready to serve as an application target; this repository does not implement
TestForge's shared runner or platform. Pin a committed AgentForge revision after reviewing
this release. Local changes are not a reproducible source pin.

## Start, verify, stop

```bash
make install
make testforge-up
make testforge-smoke
make testforge-down
```

Host endpoints: API `http://127.0.0.1:18010`, effects `http://127.0.0.1:18011`.
Containers use API port 8000, effects port 8001 and PostgreSQL port 5432. The helper sets
`MOCK_EFFECTS_PUBLIC_URL` so report links use the host-visible address. A runner inside
another container should use its own network-reachable addresses instead.

The default model is the checked-in `repo_analysis.yaml` script. It reads a fixture file,
searches, runs unittest, publishes a report, then finishes. It does not call an external
LLM. The sample has a deliberate `test_subtract_fails` failure: `COMPLETED` means the
workflow produced its report, not that the repository has no test failures.

The default sandbox image is `python:3.12-alpine`. Pull/build dependencies before a timed
scenario. Record the actual runtime image IDs/digests and dependency lockfile with release
evidence; floating image tags alone do not guarantee identical future environments.

## Readiness and state

1. Compose waits for PostgreSQL and runs Alembic once before the API/worker.
2. Require `GET /health` to return HTTP 200 and `{"status":"ok","db":"up"}`.
3. Require mock `/stats` to respond. A healthy API does not prove a worker can execute.
4. POST a known run, capture its ID, and poll that specific run with a deadline until
   `COMPLETED` or `FAILED`. Save the last state and events on timeout.
5. Retrieve the reported artifact and verify its expected body; inspect side-effect counts.
6. Capture logs before teardown. Recreate the dedicated stack between independent scenario
   groups. No production reset API is added.

`tests/` and `drills/` use destructive fixtures and belong to AgentForge's own verification.
A TestForge adapter should use public HTTP behavior and explicitly controlled worker
processes/containers. It should not import `app.*` or silently reuse the development DB.

## HTTP contracts

| Operation | Expected behavior |
|---|---|
| `POST /runs` | Body: `objective` string (default empty), `repo_path` (default `sample_repo`), `max_steps` integer 1-50 (default 10). Returns 202 with UUID `id` and `QUEUED`. |
| `GET /runs/{id}` | `id`, `objective`, `repo_path`, `status`, `lease_owner`, `lease_generation`, `lease_expires_at`, `attempts`, `max_steps`, `result`, `error`, `created_at`. Optional result/error and lease fields may be null. |
| `GET /runs/{id}/events` | Array ordered by increasing `seq`; each item includes `type`, `payload`, `created_at`. |
| Unknown UUID | Both run and events endpoints return 404. A malformed UUID returns 422. |
| Invalid repo path / max_steps | 422; the API must not enqueue an escaping/missing repository path. |
| `GET /demo` in the test target | 404; process-controlling demo routes are disabled. |
| Effects `/reports/{key}` | Published report object: `path`, `title`, `body`; missing key 404. |
| Effects `/stats` | `reports_applied`, `report_deliveries`, `duplicates_suppressed`, `notifications`. |

Record expected response shapes in the TestForge adapter; FastAPI's broadly typed response
handlers do not provide a complete field-level schema. Check status plus semantic outcomes,
not just that JSON parses. A freshly queued run may already be running by the next GET.
Events are append-only for a run; compare dynamic fields by type/ordering, not fixed values.

## Initial scenario matrix

| Risk | Assertion |
|---|---|
| Broken end-to-end execution | Known script completes and reports the deliberately failing fixture test |
| Invalid input | Out-of-root repo or invalid step limit returns 422 |
| Silent infinite execution | `max_steps=1` reaches FAILED with `MAX_STEPS_EXCEEDED` |
| Missing data | Unknown run/events return 404 |
| Incorrect worker recovery | Crash after keyed report effect, resume under a new generation, one report applied |
| Stale owner commit | Paused old worker is fenced after a new claim |
| Unsafe unkeyed retry | Crash after `notify.post`; run fails `UNKNOWN_EFFECT`, no retry of notification |
| Repeated poison execution | Attempt cap produces `POISON_RUN` |

The included `scripts/testforge_smoke.py` demonstrates the first four as a black-box
readiness check. Detailed fault cases remain in `drills/` until TestForge adds its own
external orchestration. Do not claim these internal drills as TestForge-authored tests.

For controlled recovery, create a separate scenario stack without its continuous worker,
then start a worker scoped to `--run-id UUID`. Set `AF_CRASH_AT=after_effect` only on that
worker; start a second worker without fault variables. Do not leave a normal worker racing
the crash worker. For unkeyed notification scenarios, configure the checked-in
`/srv/drills/scripts/notify.yaml` script in the container worker environment (the image
includes it), or the absolute checkout path for host workers. Keep the downstream process
alive throughout the scenario. Use real lease expiry with deadlines; no fixed sleep proves
completion. The host-mounted fixture tree and script must exist in the chosen container.

## Artifacts and independence

Save both repository commits, configuration, dependency/image versions, submitted payload,
run ID, final state, event history, report body, effects before/after and container logs.
The supplied helper writes JSON/log evidence under `reports/testforge/<project>/` and
returns nonzero on failure. TestForge can additionally publish JUnit/HTML/browser traces.

AgentForge has no required browser layer in its core acceptance contract. A later browser
suite may use the explicitly enabled local demo as a separately configured target mode.
Do not enable demo mode in the normal API acceptance stack just to create UI coverage.

No Skywatch or PawPal process is needed. Use `TF_PROJECT=agentforge-testforge-<run-id>` and
unique loopback ports for concurrent environments. All tests submit synthetic local runs;
AWS deployment and live LLM access are optional future extensions.
