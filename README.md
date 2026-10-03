# AgentForge

Crash-safe distributed AI-agent runtime built with Python, FastAPI, and PostgreSQL.

## Status: Phase 1 (v0.1)

- Four tables (`agent_runs`, `run_steps`, `tool_calls`, `run_events`) in one Alembic migration
- `transition()` checks the allowed-transition table and appends a `run_events` row in the same transaction
- Decision-first agent loop: save the decision, then the tool intent with its idempotency key, then run the tool, then save the result and checkpoint
- `FakeModel` replays scripted decisions from YAML
- Tools: `files.read` and `files.search` (contained to the repo, size-capped), `tests.run` (`docker run --network none` with the repo mounted read-only), and a placeholder `report.publish`
- Max-steps cap per run

## Quickstart

```bash
make install
make up        # Postgres 16 on localhost:5433
make migrate
make run       # API on localhost:8000
```

Submit a run against the fixture repo:

```bash
curl -s -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"objective": "Find failing tests", "repo_path": "sample_repo"}'
curl -s localhost:8000/runs/<id>
curl -s localhost:8000/runs/<id>/events
```

`repo_path` is relative to `REPOS_ROOT`, which defaults to `tests/fixtures`.

## Tests

```bash
make lint
make test      # needs `make up` and Docker; truncates the tables in DATABASE_URL
```

If your local database was created before Phase 1 (the old `schema.sql` tables), run `make reset-db` once.
