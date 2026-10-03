# tests/conftest.py
import pytest
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal, run_migrations


@pytest.fixture(scope="session")
def migrated_db():
    run_migrations()


@pytest.fixture
def db(migrated_db, tmp_path, monkeypatch):
    """Empty tables, and keep reports out of the project dir."""
    with SessionLocal.begin() as session:
        session.execute(text("TRUNCATE TABLE run_events, tool_calls, run_steps, agent_runs"))

    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
