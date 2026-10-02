from pathlib import Path

import psycopg

from app.config import settings


def ping_db() -> bool:
    try:
        with psycopg.connect(
            settings.database_url,
            connect_timeout=2,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


def init_db():
    """Reads schema.sql and executes it against the database."""
    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text()

    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            # We use a transaction block to ensure all statements execute together
            cur.execute(schema_sql)
        conn.commit()
    print("Database schema initialized.")
