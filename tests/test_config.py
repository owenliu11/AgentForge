from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import Settings


def test_database_url_is_normalized_to_psycopg():
    s = Settings(database_url="postgres://u:p@h:1/d", db_host=None)
    assert s.sqlalchemy_database_url == "postgresql+psycopg://u:p@h:1/d"


def test_db_parts_override_database_url_and_escape_the_password():
    s = Settings(
        database_url="postgresql://ignored@nowhere/x",
        db_host="db.example.internal",
        db_username="agentforge",
        db_password="p@ss/w:rd#1",
        db_name="agentforge",
    )
    url = make_url(s.sqlalchemy_database_url)

    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database) == ("db.example.internal", 5432, "agentforge")
    assert url.password == "p@ss/w:rd#1"


def test_escaped_password_survives_alembic_config():
    s = Settings(db_host="db.internal", db_username="agentforge", db_password="p@ss%w/rd")
    config = Config()

    config.set_main_option("sqlalchemy.url", s.alembic_database_url)

    url = make_url(config.get_main_option("sqlalchemy.url"))
    assert url.password == "p@ss%w/rd"
