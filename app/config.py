# app/config.py
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


class Settings(BaseSettings):
    # Port 5433 matches docker-compose.yml and .env.example.
    database_url: str = "postgresql://agentforge:agentforge@localhost:5433/agentforge"

    # Runs may only point at repos inside this directory.
    repos_root: Path = FIXTURES
    # FakeModel script; the real LLM adapter replaces this in Phase 4/5.
    model_script: Path = FIXTURES / "scripts" / "repo_analysis.yaml"
    # report.publish writes to <reports_dir>/<run_id>/.
    reports_dir: Path = PROJECT_ROOT / "reports"

    sandbox_image: str = "python:3.12-alpine"
    tests_run_timeout_seconds: int = 120

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_database_url(self) -> str:
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


settings = Settings()
