.DEFAULT_GOAL := help

COMPOSE := docker compose

# iCloud-synced folders (Desktop, Documents) turn .venv files into online-only
# placeholders, and every Python import then waits on a download. On macOS, keep
# the environment and bytecode outside the project. Set either variable to override.
ifeq ($(shell uname -s),Darwin)
export UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/agentforge
export PYTHONPYCACHEPREFIX ?= $(HOME)/.cache/agentforge/pycache
endif

.PHONY: help install up up-all down reset-db ps migrate run worker mock demo test drill lint format fix health clean

help:
	@echo "AgentForge targets"
	@echo ""
	@echo "  make install   Install dependencies with uv"
	@echo "  make up        Start Postgres 16"
	@echo "  make up-all    Build and start db, api, mock_effects, and 3 workers"
	@echo "  make down      Stop Postgres"
	@echo "  make reset-db  Delete the local database volume and re-migrate"
	@echo "  make ps        Show Docker Compose status"
	@echo "  make migrate   Apply Alembic migrations"
	@echo "  make run       Run FastAPI locally"
	@echo "  make worker    Run one worker locally (start several in separate shells)"
	@echo "  make mock      Run the mock_effects service locally"
	@echo "  make demo      Open the demo site at localhost:8000/demo"
	@echo "  make test      Run unit and lease tests"
	@echo "  make drill     Run kill drills (real worker processes)"
	@echo "  make lint      Run ruff"
	@echo "  make format    Format code with ruff"
	@echo "  make fix       Auto-fix lint issues and format"
	@echo "  make health    Check /health endpoint"
	@echo "  make clean     Remove local caches and reports"

install:
	uv python install 3.12 || true
	uv sync --group dev

up:
	$(COMPOSE) up -d --wait db

up-all:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

reset-db:
	$(COMPOSE) down -v
	$(COMPOSE) up -d --wait db
	uv run alembic upgrade head

ps:
	$(COMPOSE) ps

migrate:
	uv sync --group dev --quiet
	uv run alembic upgrade head

run: migrate
	uv run uvicorn app.main:app --reload --port 8000

worker:
	uv run python -m app.worker

mock:
	uv run uvicorn mock_effects.app:app --port 8001

demo: up migrate
	@echo "Demo site: http://localhost:8000/demo"
	@trap 'kill $$(jobs -p) 2>/dev/null' EXIT; \
	uv run uvicorn mock_effects.app:app --port 8001 --log-level warning & \
	DEMO_MODE=true uv run uvicorn app.main:app --port 8000 --log-level warning

test:
	uv run pytest tests

drill:
	uv run pytest drills -v

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

fix:
	uv run ruff check . --fix
	uv run ruff format .

health:
	curl -fsS http://localhost:8000/health

clean:
	rm -rf .pytest_cache .ruff_cache reports
