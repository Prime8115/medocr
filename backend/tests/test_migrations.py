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


def test_layout_upgrade_preserves_legacy_lessons_without_applying_them(tmp_path):
    from sqlalchemy import text

    cfg = _alembic_config(f"sqlite:///{tmp_path / 'legacy.db'}")
    command.upgrade(cfg, "b2e8f4a6c9d1")
    engine = create_engine(cfg.get_main_option("sqlalchemy.url"))
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO supplier_column_lessons
            (id, shop_id, supplier_gstin, heading, heading_printed, field, kind,
             times_used, times_undone, times_failed, active, created_at, updated_at)
            VALUES ('old-lesson', 'test-shop', '27AAGCL1995C1ZF', 'retailerrate',
                    'Retailer Rate', 'ptr', 'is', 3, 0, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT layout_signature, active, times_used FROM supplier_column_lessons WHERE id='old-lesson'"
        )).one()
        assert row == ("", 0, 3)
    engine.dispose()
