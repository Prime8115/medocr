"""Alembic migrations must apply and roll back cleanly."""
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {"shops", "users", "documents", "connectors", "push_deliveries", "audit_log"}


def _alembic_config(db_url: str) -> Config:
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_head_then_downgrade_base(tmp_path):
    db_file = tmp_path / "migrate_test.db"
    db_url = f"sqlite:///{db_file}"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)

    command.downgrade(cfg, "base")
    tables_after = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.isdisjoint(tables_after)
    engine.dispose()
