"""A fresh install must work, and so must going back. Runs the migrations
up, down and up again on a database of its own, and checks the models agree."""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from conftest import TEST_DATABASE_URL

MIGRATION_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/tms_migration_check"
TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
          "consignment_event", "inbound_event", "run", "stop", "outbound_call", "print_job",
          "api_key", "alembic_version"}


@pytest.fixture
def blank_database(monkeypatch):
    admin = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    name = MIGRATION_URL.rsplit("/", 1)[1]
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'drop database if exists "{name}" with (force)'))
        conn.execute(text(f'create database "{name}"'))
    engine.dispose()
    # alembic/env.py prefers this over the ini, so point it at the blank one
    monkeypatch.setenv("TMS_DATABASE_URL", MIGRATION_URL)
    yield MIGRATION_URL
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'drop database if exists "{name}" with (force)'))
    engine.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text(
            "select table_name from information_schema.tables where table_schema='public'"
        )).scalars())


def test_migrations_go_up_down_and_up_again(blank_database):
    cfg = alembic_config(blank_database)
    engine = create_engine(blank_database)

    command.upgrade(cfg, "head")
    assert _tables(engine) == TABLES
    with engine.connect() as conn:
        triggers = conn.execute(text(
            "select tgname from pg_trigger where not tgisinternal")).scalars().all()
    assert "consignment_event_append_only" in triggers

    command.downgrade(cfg, "base")
    assert _tables(engine) == {"alembic_version"}

    command.upgrade(cfg, "head")
    assert _tables(engine) == TABLES
    engine.dispose()


def test_the_models_match_the_migrations(blank_database):
    """No drift: what the models say is what a fresh database gets."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from tms.models import Base

    command.upgrade(alembic_config(blank_database), "head")
    engine = create_engine(blank_database)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], f"the models and the migrations disagree: {diff}"
