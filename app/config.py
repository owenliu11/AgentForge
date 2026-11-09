# app/config.py
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


class Settings(BaseSettings):
    # Port 5433 matches docker-compose.yml and .env.example.
    database_url: str = "postgresql://agentforge:agentforge@localhost:5433/agentforge"

    # Runs may only point at repos inside this directory.
    repos_root: Path = FIXTURES
    # Deterministic FakeModel script; no live LLM adapter is shipped.
    model_script: Path = FIXTURES / "scripts" / "repo_analysis.yaml"
    # Report paths are validated beneath this root; content lives in mock_effects.
    reports_dir: Path = PROJECT_ROOT / "reports"

    sandbox_image: str = "python:3.12-alpine"
    tests_run_timeout_seconds: int = 120

    # Leases (Phase 2). A worker renews every heartbeat_seconds; a lease that
    # isn't renewed for lease_seconds can be claimed by another worker.
    lease_seconds: float = 30.0
    heartbeat_seconds: float = 10.0
    # How long an idle worker waits before polling agent_runs again.
    poll_seconds: float = 1.0
    # Poison protection (Phase 3): a run claimed more than this many times fails.
    max_attempts: int = 3

    # report.publish and notify.post send their side effects here (Phase 3).
    mock_effects_url: str = "http://localhost:8001"
    # Optional address returned to host clients when the transport URL is container-only.
    mock_effects_public_url: str = ""

    # Serves the demo page, which starts worker processes. Local use only: leave
    # this off anywhere the API is reachable by other people.
    demo_mode: bool = False

    # Alternative to DATABASE_URL for the cloud: RDS keeps the master password in
    # Secrets Manager, and ECS injects its fields as separate env vars. When
    # DB_HOST is set, these build the URL (the password is URL-escaped).
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "agentforge"
    db_username: str | None = None
    db_password: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.db_host:
            return URL.create(
                "postgresql+psycopg",
                username=self.db_username,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                database=self.db_name,
            ).render_as_string(hide_password=False)

        url = self.database_url

        if url.startswith("postgresql+psycopg://"):
            return url

        if url.startswith("postgresql://"):
            return url.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )

        if url.startswith("postgres://"):
            return url.replace(
                "postgres://",
                "postgresql+psycopg://",
                1,
            )

        return url

    @property
    def alembic_database_url(self) -> str:
        # Alembic's config treats % as interpolation, and an escaped password has %.
        return self.sqlalchemy_database_url.replace("%", "%%")


settings = Settings()
