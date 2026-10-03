# app/db.py
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import PROJECT_ROOT, settings

engine = create_engine(
    settings.sqlalchemy_database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def run_migrations() -> None:
    """
    Programmatically run Alembic migrations (used by tests).

    The API does not call this on startup: with several workers booting at
    once, migrations must run as a single one-off step (`make migrate`).
    """
    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    alembic_cfg.set_main_option(
        "sqlalchemy.url",
        settings.sqlalchemy_database_url,
    )
    command.upgrade(alembic_cfg, "head")


def ping_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
