# Running AgentForge: a hands-on walkthrough

**Quickest way to see it:** after the one-time setup below, run `make demo` and open <http://localhost:8000/demo>. Pick a scenario and press Run: the page starts real workers, crashes them on purpose, and shows the workers, the lease, the side-effect counters, and the event log live. See [the demo site](#the-demo-site).

This guide also takes you from a fresh checkout to watching a worker crash mid-run and another worker finish the job with zero duplicate side effects. It takes about 15 minutes.

1. [One-time setup](#1-one-time-setup)
2. [Run the tests](#2-run-the-tests)
3. [Run the kill drills](#3-run-the-kill-drills)
4. [Watch one run, step by step](#4-watch-one-run-step-by-step)
5. [Crash a worker and watch recovery](#5-crash-a-worker-and-watch-recovery)
6. [Run everything in Docker with 3 workers](#6-run-everything-in-docker-with-3-workers)
7. [Cleaning up and troubleshooting](#7-cleaning-up-and-troubleshooting)

---

## The demo site

```bash
make demo
```

This starts Postgres, applies migrations, and runs `mock_effects` and the API with `DEMO_MODE=true`. Stop it with Ctrl-C. Stop any `make worker`, `make run`, `make mock`, or `make up-all` first: they use the same ports, and a separate worker could grab the demo's run.

| Scenario | What happens | Expected result |
|---|---|---|
| Normal run | One worker runs the agent | COMPLETED, 1 report applied |
| Crash: `after_decision` / `after_intent` | Worker A dies before sending the report; worker B takes over | COMPLETED, 1 delivery, 1 applied |
| Crash: `after_effect` / `before_commit` | Worker A dies after sending the report | COMPLETED, 2 deliveries, 1 applied |
| Stale worker | Worker A freezes, B finishes, A wakes up | COMPLETED, A's write is rejected |
| Unknown effect | Worker A dies after a notification with no idempotency key | FAILED with `UNKNOWN_EFFECT`, 1 notification |
| Poison run | Three workers in a row crash | FAILED with `POISON_RUN` after 4 claims |

Workers use a 3-second lease here so a takeover happens quickly. Each scenario resets the `mock_effects` counters and adds a run to your local database. Worker JSON logs appear in the `make demo` terminal.

`DEMO_MODE` lets the API start worker processes, so it's off by default and meant for your own machine only. Don't turn it on in AWS.

---

## 1. One-time setup

You need **Docker Desktop** (running) and **uv** (`brew install uv`).

> **iCloud note.** On macOS, `make` commands keep the Python environment in `~/.venvs/agentforge`, not the project's `.venv`. iCloud-synced folders turn `.venv` files into online-only placeholders, and imports can then hang for 10+ minutes. If you use `uv run` directly (as some commands below do), first run `export UV_PROJECT_ENVIRONMENT=~/.venvs/agentforge` in that terminal.

```bash
make install
```

```bash
make up
```

```bash
make migrate
```

```bash
docker pull python:3.12-alpine
```

What each one does:

- `make install` installs Python 3.12 and the dependencies into `.venv`.
- `make up` starts Postgres 16 in Docker on `localhost:5433`.
- `make migrate` creates the four tables: `agent_runs`, `run_steps`, `tool_calls`, `run_events`.
- The `docker pull` fetches the sandbox image that `tests.run` uses to run the fixture repo's tests with no network.

Check the database is reachable:

```bash
make run
```

Then, in another terminal:

```bash
make health
```

You should see `{"status":"ok","db":"up"}`. Stop the API with Ctrl-C.

---

## 2. Run the tests

```bash
make lint
```

```bash
make test
```

Expected: `22 passed` in about 20 seconds. What they cover:

| File | What it proves |
|---|---|
| `tests/test_phase1.py` | Illegal state transitions raise; a scripted run completes the same way twice; `../../etc/passwd` is rejected; the step cap fails a looping run; `POST /runs` against the fixture repo produces a report naming the failing test |
| `tests/test_phase2.py` | 8 threads race for one run and exactly one wins; a write with a stale lease generation is rejected; a stopped heartbeat lets another worker take over; 20 runs across 3 worker processes, none claimed twice |
| `tests/test_recovery.py` | A downstream outage leaves the run retryable, and a later claim finishes it with the same idempotency key |
| `tests/test_config.py` | Database URL handling, including the cloud-style `DB_*` variables |
| `tests/test_health.py` | `/health` reports the database status |

> Tests and drills **truncate the tables** in your database. Don't point `DATABASE_URL` at anything you care about.

Useful pytest flags:

```bash
uv run pytest tests/test_phase2.py -v
```

```bash
uv run pytest tests -k stale -v -s
```

`-v` lists each test, `-k` filters by name, and `-s` shows print and log output.

---

## 3. Run the kill drills

```bash
make drill
```

Expected: `8 passed` in about 40 seconds. Each drill starts real worker processes. Worker A is told to die (`os._exit(137)`, like `kill -9`) at a specific line. Worker B waits for A's lease to expire, claims the run as generation 2, and finishes it. The drill then asserts the final status and asks `mock_effects` how many times the report was actually applied.

| Drill | A dies… | Expected outcome |
|---|---|---|
| `after_decision` | after saving the model's decision | B replays the saved decision; report applied once |
| `after_intent` | after saving the tool intent | B re-runs the tool with the same key; report applied once |
| `after_effect` | after the report was sent, before saving the result | B re-sends with the same key; downstream dedupes it (2 deliveries, 1 applied) |
| `before_commit` | inside the result transaction, before commit | same as above |
| stale worker | A freezes (`SIGSTOP`), B takes over, A wakes up | A's write is rejected (`LostLease`); report applied once |
| poison run | three workers in a row crash on the same run | the 4th claim fails it as `POISON_RUN` |
| unknown effect | right after `notify.post`, which has no idempotency key | B fails the run with `UNKNOWN_EFFECT` instead of risking a second notification |
| recovery time | after the effect | B's claim lands within lease length + poll interval |

To run a single drill:

```bash
uv run pytest drills -k after_effect -v
```

Each worker's JSON log is written to a file under pytest's temporary directory, and it's printed in the failure message if a drill fails. To watch the logs live, do the same thing by hand in step 5.

---

## 4. Watch one run, step by step

Open three terminals in the project folder.

**Terminal 1** — the downstream service that receives reports and counts duplicates:

```bash
make mock
```

**Terminal 2** — the API:

```bash
make run
```

**Terminal 3** — submit a run. Nothing processes it yet, because no worker is running:

```bash
curl -s -X POST localhost:8000/runs -H 'content-type: application/json' -d '{"objective": "Find failing tests", "repo_path": "sample_repo"}'
```

You get back `{"id": "<run-id>", "status": "QUEUED"}`. Save the id:

```bash
RUN=<paste-the-id-here>
```

Now start a worker in Terminal 3 (or a 4th terminal):

```bash
make worker
```

Its JSON log lines show it claiming the run (`"event": "claimed"`, `"generation": 1`) and completing it. Stop it with Ctrl-C, then inspect the result:

```bash
curl -s localhost:8000/runs/$RUN | python3 -m json.tool
```

```bash
curl -s localhost:8000/runs/$RUN/events | python3 -m json.tool
```

The run is `COMPLETED`, and the events show the decision-first order for each of the 5 steps: `step_decided` → `tool_intended` → `tool_succeeded`. The agent's script is `tests/fixtures/scripts/repo_analysis.yaml`: read the README, search the code, run the tests in a sandbox, publish a report, finish.

Read the published report (the URL is in the run's `result.reports`):

```bash
curl -s localhost:8000/runs/$RUN | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["reports"][0])'
```

```bash
curl -s <that-url> | python3 -c 'import sys,json; print(json.load(sys.stdin)["body"])'
```

It names the failing test: `tests.test_math.TestMath.test_subtract_fails`.

---

## 5. Crash a worker and watch recovery

This is the core of the project, done by hand. Keep `make mock` and `make run` running from step 4. Use a 5-second lease so you don't wait 30 seconds.

**1. Start a worker that will crash right after publishing the report:**

```bash
AF_CRASH_AT=after_effect LEASE_SECONDS=5 uv run python -m app.worker --worker-id worker-a
```

**2. Submit a run from another terminal:**

```bash
curl -s -X POST localhost:8000/runs -H 'content-type: application/json' -d '{"objective": "crash demo"}'
```

```bash
RUN=<paste-the-id-here>
```

Worker A claims the run, works through steps 1–3, sends the report in step 4, then logs `"msg": "crash point"` and exits with code 137. Confirm with `echo $?` in that terminal.

**3. Look at the orphaned run.** It's still `RUNNING`, owned by a dead worker, with generation 1:

```bash
curl -s localhost:8000/runs/$RUN | python3 -m json.tool
```

**4. Start a healthy worker:**

```bash
LEASE_SECONDS=5 uv run python -m app.worker --worker-id worker-b
```

Within about 5 seconds (once A's lease expires) worker B claims the run as generation 2 and finishes it.

**5. Check the outcome:**

```bash
curl -s localhost:8000/runs/$RUN/events | python3 -c 'import sys,json; [print(e["seq"], e["type"], e["payload"]) for e in json.load(sys.stdin)]'
```

```bash
curl -s localhost:8001/stats
```

This is real output from this exact sequence (payloads trimmed):

```
 2 run_claimed     {'worker': 'worker-a', 'generation': 1}
 ...
12 step_decided    {'seq': 4, 'tool': 'report.publish'}
13 tool_intended   {'seq': 4, 'tool': 'report.publish'}
14 run_claimed     {'worker': 'worker-b', 'generation': 2}     <- A died; B takes over
15 run_resumed     {'seq': 4, 'generation': 2, 'replayed_decision': True}
16 tool_retried    {'seq': 4, 'tool': 'report.publish'}        <- same idempotency key
17 tool_succeeded  {'seq': 4, 'tool': 'report.publish'}
18 step_decided    {'seq': 5}
19 run_completed   {}

{"reports_applied":1,"report_deliveries":2,"duplicates_suppressed":1,"notifications":0}
```

How to read it: the report was **sent twice** (once by A before it died, once by B on retry) but **applied once**. Both requests carried the same idempotency key, so the downstream recognized the second one as a duplicate. That's "effectively-once" for a keyed tool.

**Try the other crash points.** Repeat with `AF_CRASH_AT` set to `after_decision`, `after_intent`, or `before_commit`, and compare `report_deliveries` (it's 1 when A dies before sending). Call `curl -s -X POST localhost:8001/reset` between tries to zero the counters.

**Other knobs:**

| Env var | Effect |
|---|---|
| `AF_CRASH_AT=<point>` | `os._exit(137)` at that line |
| `AF_STALL_AT=<point>` | freeze the whole process (`SIGSTOP`), simulating a hung VM; resume it with `kill -CONT <pid>` |
| `AF_CRASH_TOOL=<tool>` | which tool's step triggers the crash (default `report.publish`) |
| `LEASE_SECONDS`, `HEARTBEAT_SECONDS` | lease length and renewal interval (defaults 30 and 10) |
| `MAX_ATTEMPTS` | claims allowed before a run fails as `POISON_RUN` (default 3) |

---

## 6. Run everything in Docker with 3 workers

Stop the local `make mock` / `make run` / workers first, since they use the same ports.

```bash
make up-all
```

This builds the image from the `Dockerfile` and starts Postgres, a one-off migration, `mock_effects`, the API, and **3 worker replicas**. Then submit a run exactly as in step 4 and watch the workers:

```bash
docker compose logs -f worker
```

Each log line is prefixed by its container (`worker-1`, `worker-2`, `worker-3`). Submit several runs and you'll see them spread across workers.

**Kill the worker that owns a run.** Submit a run, check `lease_owner` in `GET /runs/$RUN` while it's `RUNNING`, find that container (`docker compose ps`, then match the hostname with `docker ps`), and run `docker kill <container>`. With the default 30-second lease, another worker claims generation 2 after about 30 seconds. This is the same drill you'll run in AWS with `aws ecs stop-task` in Phase 4. Tip: a run takes only a couple of seconds here, so submit a few runs at once to have a better chance of catching one mid-flight.

Stop the stack:

```bash
make down
```

---

## 7. Cleaning up and troubleshooting

| Problem | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Start Docker Desktop |
| A command hangs at the start, with near-zero CPU | It's using the iCloud-evicted `.venv`; see the note in step 1 |
| `relation "agent_runs" does not exist` | `make migrate` |
| Schema errors after pulling changes | `make reset-db` (deletes local data and re-migrates) |
| `address already in use` on 8000/8001 | Something else (or `make up-all`) holds the port; stop it |
| `tests.run` returns `Docker is not available` | Start Docker Desktop and pull `python:3.12-alpine` |
| A run stays `QUEUED` | No worker is running; start one with `make worker` |

`make clean` removes caches, and `make down` stops the containers.
