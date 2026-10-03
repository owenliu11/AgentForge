.DEFAULT_GOAL := help

COMPOSE := docker compose

.PHONY: help install up down reset-db ps migrate run test drill lint format fix health clean

help:
	@echo "AgentForge targets"
	@echo ""
	@echo "  make install   Install dependencies with uv"
	@echo "  make up        Start Postgres 16"
	@echo "  make down      Stop Postgres"
	@echo "  make reset-db  Delete the local database volume and re-migrate"
	@echo "  make ps        Show Docker Compose status"
	@echo "  make migrate   Apply Alembic migrations"
	@echo "  make run       Run FastAPI locally"
	@echo "  make test      Run pytest"
	@echo "  make drill     Run kill drills (Phase 3)"
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

down:
	$(COMPOSE) down

reset-db:
	$(COMPOSE) down -v
	$(COMPOSE) up -d --wait db
	uv run alembic upgrade head

ps:
	$(COMPOSE) ps

migrate:
	uv run alembic upgrade head

run: migrate
	uv run uvicorn app.main:app --reload --port 8000

test:
	uv run pytest

drill:
	@echo "No drills until Phase 3"

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
