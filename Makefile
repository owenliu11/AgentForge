.DEFAULT_GOAL := help

COMPOSE := docker compose
TERRAFORM := $(shell command -v terraform || echo $(HOME)/.local/bin/terraform)

# iCloud-synced folders (Desktop, Documents) turn .venv files into online-only
# placeholders, and every Python import then waits on a download. On macOS, keep
# the environment and bytecode outside the project. Set either variable to override.
ifeq ($(shell uname -s),Darwin)
export UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/agentforge
export PYTHONPYCACHEPREFIX ?= $(HOME)/.cache/agentforge/pycache
# Where the Terraform release binary is installed.
export PATH := $(HOME)/.local/bin:$(PATH)
endif

.PHONY: help install up up-all down reset-db ps migrate run worker mock demo test drill measure bootstrap deploy destroy cloud-drill tf-check lint format fix health clean verify testforge-up testforge-smoke testforge-down

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
	@echo "  make test      Run tests in a disposable database"
	@echo "  make drill     Run kill drills in a disposable database"
	@echo "  make verify    Run lint and all tests/drills in isolation"
	@echo "  make testforge-up / testforge-smoke / testforge-down"
	@echo "  make measure   Measure recovery time over 20 crashes"
	@echo "  make bootstrap   Create optional AWS foundations (once)"
	@echo "  make deploy      Build AgentForge in AWS (this costs money)"
	@echo "  make cloud-drill Stop a live worker task and check recovery"
	@echo "  make destroy     Tear down everything make deploy built"
	@echo "  make tf-check    Check Terraform formatting and validity"
	@echo "  make lint      Run ruff"
	@echo "  make format    Format code with ruff"
	@echo "  make fix       Auto-fix lint issues and format"
	@echo "  make health    Check /health endpoint"
	@echo "  make clean     Remove local caches and reports"

install:
	uv python install 3.12 || true
	uv sync --frozen --group dev

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
	uv sync --frozen --group dev --quiet
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
	./scripts/check.sh tests

drill:
	./scripts/check.sh drills

measure:
	./scripts/check.sh measure

bootstrap:
	infra/bootstrap.sh

deploy:
	infra/deploy.sh

destroy:
	infra/destroy.sh

cloud-drill:
	infra/cloud_drill.sh

tf-check:
	$(TERRAFORM) fmt -check -recursive infra
	$(TERRAFORM) -chdir=infra init -backend=false -input=false
	$(TERRAFORM) -chdir=infra validate
	$(TERRAFORM) -chdir=infra test
	$(TERRAFORM) -chdir=infra/bootstrap init -backend=false -input=false
	$(TERRAFORM) -chdir=infra/bootstrap validate

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

verify: lint
	./scripts/check.sh all

testforge-up:
	./scripts/testforge.sh up

testforge-smoke:
	./scripts/testforge.sh smoke

testforge-down:
	./scripts/testforge.sh down
