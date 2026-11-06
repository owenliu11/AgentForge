# AgentForge

Crash-safe distributed AI-agent runtime built with Python, FastAPI, and PostgreSQL.

Kill a worker at any point and another worker finishes the run, with zero duplicate side effects.

## Status: Phase 3 (v0.3)

- **Durable runs (Phase 1).** Four tables (`agent_runs`, `run_steps`, `tool_calls`, `run_events`); a decision-first agent loop; `FakeModel` replays scripted decisions; tools are path-contained and `tests.run` runs in `docker run --network none` with the repo read-only.
- **Workers and leases (Phase 2).** Workers claim runs straight from Postgres with `FOR UPDATE SKIP LOCKED`, renew a 30 s lease every 10 s, and fence every write with `lease_generation`. A zombie worker's write updates 0 rows and raises `LostLease`.
- **Recovery and kill drills (Phase 3).** A claim resumes at the first missing record. `report.publish` posts to `mock_effects` with an idempotency key; `notify.post` has no key, so an unknown outcome fails the run with `UNKNOWN_EFFECT`. A run claimed more than 3 times fails as `POISON_RUN`. `make drill` kills real worker processes at four crash points.

## Quickstart

Fastest way to see it: `make install`, then `make demo`, then open <http://localhost:8000/demo>. Pick a scenario and watch real workers crash and recover in the browser.

[RUNNING.md](RUNNING.md) is a step-by-step walkthrough: run the tests and drills, watch a run, and crash a worker by hand to see recovery.

```bash
make install
make up        # Postgres 16 on localhost:5433
make migrate
make mock      # mock_effects on localhost:8001   (shell 2)
make worker    # one worker; start more in more shells
make run       # API on localhost:8000             (shell 3)
```

Or everything in containers (one image from the `Dockerfile`), with three workers: `make up-all`. Workers start `tests.run` sandboxes through the host's Docker socket.

Submit a run against the fixture repo:

```bash
curl -s -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"objective": "Find failing tests", "repo_path": "sample_repo"}'
curl -s localhost:8000/runs/<id>
curl -s localhost:8000/runs/<id>/events
curl -s localhost:8001/stats      # reports applied, duplicates suppressed
```

`repo_path` is relative to `REPOS_ROOT`, which defaults to `tests/fixtures`.

## Tests and drills

```bash
make lint
make test      # unit + lease tests; needs `make up` and Docker
make drill     # kill drills with real worker processes
```

Both truncate the tables in `DATABASE_URL`. If your local database predates Phase 1, run `make reset-db` once.

| Drill | What happens | Asserted |
|---|---|---|
| `after_decision`, `after_intent`, `after_effect`, `before_commit` | Worker A calls `os._exit(137)` at that line on the `report.publish` step; worker B claims generation 2 and resumes | Run COMPLETED, 5 steps, one tool-call row per step, report applied exactly once, saved decision replayed |
| Stale worker | A freezes (`SIGSTOP`, heartbeat included) right after publishing; B takes over and finishes; A is resumed | A's write is fenced off (`LostLease`), report applied once, duplicate suppressed |
| Poison run | Three workers in a row crash on the same run | 4th claim fails it as `POISON_RUN`, `attempts = 4` |
| Unknown effect | A crashes right after the unkeyed `notify.post` | B fails the run with `UNKNOWN_EFFECT`; one notification, never re-sent |
| Recovery time | Kill to next claim | Within the lease length plus poll interval |

Drills use a 2 s lease (`LEASE_SECONDS`) so they run in about 30 seconds.

### Recovery time

Measured with `make measure`: the `after_effect` crash, repeated 20 times with real worker processes, from the worker's death to the next worker's claim, using the database clock. Raw samples are in [drills/results/recovery.json](drills/results/recovery.json).

| Lease | Runs | p50 | p95 | Max | Extra reports applied |
|---|---|---|---|---|---|
| 2 s | 20 | 2.01 s | 2.03 s | 2.04 s | 0 |

Recovery time tracks the lease length, because the next worker can only claim once the lease expires. With the default 30 s lease, expect about 30 s. A shorter lease recovers faster, but a brief stall is more likely to trigger an unnecessary takeover. Measured on a local machine.

## Design

### Claim, heartbeat, fence

The claim picks a queued run or one whose lease expired, so it is also the recovery trigger; there is no separate queue or reconciler.

```sql
UPDATE agent_runs
SET lease_owner = :worker, lease_generation = lease_generation + 1,
    lease_expires_at = now() + make_interval(secs => :lease_seconds),
    status = 'RUNNING', attempts = attempts + 1
WHERE id = (SELECT id FROM agent_runs
            WHERE status = 'QUEUED' OR (status = 'RUNNING' AND lease_expires_at < now())
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, lease_generation;
```

Every worker transaction starts with `UPDATE agent_runs ... WHERE id = :run_id AND lease_generation = :gen`; 0 rows means `LostLease` and a rollback. All times use the database clock.

### Commit order and recovery

Each step writes, one fenced transaction each: (1) decision, (2) tool intent with its key, (3) run the tool, (4) result plus checkpoint.

| Crash point | What recovery does |
|---|---|
| Before the decision is saved | Ask the model again |
| After decision, before intent | Replay the saved decision; don't re-ask the model |
| After intent, before result, keyed or read-only tool | Re-run with the same key; the downstream dedupes it |
| After intent, before result, unkeyed tool | Mark UNKNOWN; fail with `UNKNOWN_EFFECT` |
| After result | Continue with the next step |

Idempotency key: `sha256(run_id | step_seq | tool | canonical_json(args))`.

### Failure semantics

Execution is at-least-once. Side effects are effectively-once for tools whose downstream dedupes by idempotency key. For tools that can't take a key, AgentForge refuses to guess and fails the run.

Infrastructure errors (database unreachable, downstream connection error or 5xx) don't fail a run: the worker drops it without writing, the lease expires, and the next claim resumes it. The lease length is the backoff and the attempts cap bounds the retries. Errors that are the run's own fault (bad decision, step cap, 4xx from a downstream) fail it immediately.

### Configuration

All settings are env vars (see `.env.example`). Locally, `DATABASE_URL` points at the compose Postgres. In the cloud, set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, and `DB_PASSWORD` instead (for example injected from the RDS-managed secret); when `DB_HOST` is set it takes precedence.

CI (`.github/workflows/ci.yml`) runs ruff, `make test`, and `make drill` against a Postgres service container, and builds and smoke-tests the Docker image.
