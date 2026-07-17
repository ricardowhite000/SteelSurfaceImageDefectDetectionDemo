from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event


def make_engine(database_url: str) -> Engine:
    engine = create_engine(database_url, future=True)
    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


def _alembic_config(database_url: str) -> Config:
    config = Config()
    migrations = Path(__file__).resolve().parent / "migrations"
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str) -> None:
    if database_url.startswith("sqlite:///"):
        database_path = Path(database_url.removeprefix("sqlite:///"))
        database_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(database_url), "head")


def database_version(database_url: str) -> tuple[str | None, str]:
    config = _alembic_config(database_url)
    head = ScriptDirectory.from_config(config).get_current_head()
    engine = make_engine(database_url)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    except Exception:
        current = None
    finally:
        engine.dispose()
    return current, head


def require_current_database(database_url: str) -> None:
    current, head = database_version(database_url)
    if current != head:
        raise RuntimeError(
            f"数据库版本未就绪（current={current or 'none'}, head={head}）。"
            "请先运行：steel-platform db upgrade --config <yaml>"
        )
