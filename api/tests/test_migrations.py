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
          "api_key", "inbound_message", "pod_file", "pod",
          "checklist_item", "prestart_check", "prestart_item", "vehicle_record",
          "position", "carrier", "manifest", "booking", "alembic_version"}


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
    assert {"consignment_event_append_only", "prestart_check_append_only",
            "prestart_item_append_only", "vehicle_record_append_only"} <= set(triggers)

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


def _driver_row(conn, code: str) -> None:
    conn.execute(text("insert into driver (code, name, pin_hash, failed_attempts, locked, active) "
                      "values (:c, :c, 'x', 0, false, true)"), {"c": code})


def test_existing_driver_codes_become_lower_case(blank_database):
    from sqlalchemy.exc import IntegrityError

    cfg = alembic_config(blank_database)
    command.upgrade(cfg, "0003")
    engine = create_engine(blank_database)
    with engine.begin() as conn:
        _driver_row(conn, "KIM-07")
        _driver_row(conn, "sam")
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert sorted(conn.execute(text("select code from driver")).scalars()) == ["kim-07", "sam"]
    with pytest.raises(IntegrityError, match="ck_driver_code_lower"):
        with engine.begin() as conn:
            _driver_row(conn, "MEL")
    engine.dispose()


def test_codes_that_differ_only_by_case_stop_the_upgrade_and_say_which(blank_database):
    cfg = alembic_config(blank_database)
    command.upgrade(cfg, "0003")
    engine = create_engine(blank_database)
    with engine.begin() as conn:
        _driver_row(conn, "Kim")
        _driver_row(conn, "KIM")
    with pytest.raises(RuntimeError, match="differ only by case") as refused:
        command.upgrade(cfg, "head")
    assert "Kim" in str(refused.value) and "KIM" in str(refused.value)
    with engine.connect() as conn:
        assert sorted(conn.execute(text("select code from driver")).scalars()) == ["KIM", "Kim"]
    engine.dispose()
