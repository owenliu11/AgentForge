.DEFAULT_GOAL := help

COMPOSE := docker compose

.PHONY: help install up down ps run test lint format health clean

help:
	@echo "AgentForge Phase 0 targets"
	@echo ""
	@echo "  make install   Install dependencies with uv"
	@echo "  make up        Start Postgres 16"
	@echo "  make down      Stop Postgres"
	@echo "  make ps        Show Docker Compose status"
	@echo "  make run       Run FastAPI locally"
	@echo "  make test      Run pytest"
	@echo "  make lint      Run ruff"
	@echo "  make format    Format code with ruff"
	@echo "  make health    Check /health endpoint"
	@echo "  make clean     Remove local caches"

install:
	uv python install 3.12 || true
	uv sync --group dev

up:
	$(COMPOSE) up -d --wait db

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

run:
	uv run uvicorn app.main:app --reload --port 8000

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

health:
	curl -fsS http://localhost:8000/health

clean:
	rm -rf .pytest_cache .ruff_cache

fix:
	uv run ruff check . --fix
	uv run ruff format . --fix