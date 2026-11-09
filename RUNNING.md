# Running AgentForge

## Choose the workflow

| Goal | Command | State |
|---|---|---|
| Check all runtime behavior | `make verify` | Fresh disposable database; removed on exit |
| Run only focused tests / drills | `make test` / `make drill` | Same isolated behavior |
| Prepare the TestForge target | `make testforge-up` | Dedicated persistent test stack until teardown |
| Verify the external contract | `make testforge-smoke` | Adds synthetic runs and saves diagnostics |
| Remove that test target | `make testforge-down` | Deletes only its test stack and test volume |
| Watch recovery interactively | `make demo` | Development DB; single demo session |

Install Docker/Compose and uv, start Docker, then run `make install` from the repository
root. Dependency installation uses the committed lockfile. On macOS, the Makefile keeps
uv's environment outside iCloud-synced Desktop folders; override `UV_PROJECT_ENVIRONMENT`
if needed. Do not activate an unrelated Python environment.

## TestForge environment

The independent `compose.testforge.yaml` starts PostgreSQL, a one-off migration task,
mock effects, the API and one worker. The API and mock service bind to loopback on 18010
and 18011. PostgreSQL is not exposed to the host. `DEMO_MODE=false`.

```bash
make testforge-up
make testforge-smoke
make testforge-down
```

Use the same environment variables for each command when changing the project/ports:

```bash
export TF_PROJECT=agentforge-testforge-second
export TF_API_PORT=18110
export TF_EFFECTS_PORT=18111
make testforge-up
make testforge-smoke
make testforge-down
```

Keep scenarios serial within a stack. For a fresh scenario group, tear down and restart;
this resets PostgreSQL, mock counters and process memory. Never reset the mock service
while workers have active runs. `TF_PROJECT` must stay in the helper's dedicated namespace.
The host fixture path is resolved by the helper so Docker sibling containers can mount it.

If startup fails, run `./scripts/testforge.sh logs` and `make testforge-down` after collecting
evidence. The smoke script saves its last observed run state even when a check fails.
Its full success case intentionally reports a failing fixture test; workflow completion
and test success are different outcomes.

## Developer demo

```bash
make demo
```

Visit `http://localhost:8000/demo`. The local developer database uses port 5433 and is
retained after stopping the demo. The demo starts and kills worker processes; use it only
locally and avoid other workers targeting that database. It resets the shared mock
service's counters for each scenario. Ctrl-C stops the API/mock processes. `make down`
stops the normal Compose stack and retains its database volume.

## Separate local processes

```bash
make up
make migrate
make mock      # terminal 1
make worker    # terminal 2; optional more workers in other terminals
make run       # terminal 3
```

The API is at port 8000 and mock effects at 8001. A POST to `/runs` queues work; a running
worker is required for completion. `GET /health` checks DB connectivity, not worker progress.
`make reset-db` deletes the normal development database volume; it is intentionally separate
from the disposable test workflow.

`make up-all` is the original all-container developer stack with three workers. Run it from
the repository root, because its fixture mounts use the host working directory. Run
`docker pull python:3.12-alpine` before the first scenario if an image pull would interfere
with timing measurements. Do not combine the all-container stack with host processes on
the same ports.

## Verification artifacts

`make verify` saves JUnit, DB container logs and pytest temporary files (including drill
worker logs) under `reports/checks/<unique-project>/`. The helper preserves test failures
and removes only its uniquely named database stack. A cleanup failure is also reported.
Docker images are cached and intentionally retained.

To run pytest against a separately provisioned database, set `DATABASE_URL` to a database
whose name ends in `_test`, set `DB_HOST=` to prevent a cloud override, and explicitly set
`AGENTFORGE_ALLOW_TEST_RESET=1`. The test suite truncates its four runtime tables. Prefer
the Make targets, which supply an isolated database automatically. Do not run scenarios
in parallel against the same tables.

The checked-in recovery measurement is historical. `make measure` produces new samples
in an isolated database and writes them under `reports/checks/`; it does not replace the
historical evidence. Measurements are optional and are not part of `make verify`.

## Troubleshooting

| Symptom | Inspect |
|---|---|
| Docker unavailable | Start Docker; verify `docker info` |
| Port already allocated | Choose unused `TF_API_PORT` / `TF_EFFECTS_PORT`; keep both commands consistent |
| Healthy API but queued run | Worker logs, migration result, script path and claim events |
| Run completes but report describes an error | Inspect tool results; Docker launch errors are not passing tests |
| Container cannot see fixture repo | Host path sharing and the helper's absolute mount path; do not use a remote Docker daemon |
| Destructive-fixture guard refuses to run | Use `make test` or `make drill`; never disable the guard for a normal database |
| Report URL cannot be opened on the host | Set `MOCK_EFFECTS_PUBLIC_URL` to the host-visible effects address |
| Unexpected repeated reports after restart | Mock idempotency state is in memory and was lost; retain the mock process during a drill |

For AWS, use the separate [cloud guide](infra/README.md). No cloud account is needed for
local verification or TestForge integration.
