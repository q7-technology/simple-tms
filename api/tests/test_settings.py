"""Settings an admin gives on screen in place of .env's: every one has a
kind, and the API and the worker read them back without a restart."""
import logging
from datetime import UTC, datetime

from tms import config, worker
from tms.config import Settings, get_settings
from tms.models import Setting
from tms.services import runtime_settings


def test_every_setting_has_a_kind_and_none_is_left_out():
    """A new TMS_* setting must be put in one of the three kinds."""
    named = set(runtime_settings.ON_SCREEN) | set(runtime_settings.SECRETS) | set(runtime_settings.FILE_ONLY)
    assert named == set(Settings.model_fields)
    assert not set(runtime_settings.ON_SCREEN) & set(runtime_settings.SECRETS)


def test_the_worker_takes_a_change_made_elsewhere_without_a_restart(db, monkeypatch):
    """The API saves it in another process; the worker reads it again within
    REFRESH_SECONDS, and not before (one query each time, not one a call)."""
    clock = [1000.0]
    monkeypatch.setattr(config, "_now", lambda: clock[0])
    monkeypatch.setattr(config, "REFRESH_SECONDS", 5.0)
    config.forget_settings()
    assert worker.poll_seconds() == 5.0 and not worker._printing_set_up()

    now = datetime.now(UTC)
    db.add_all([Setting(name="worker_poll_seconds", value="2.0", changed_by="leighton", changed_at=now),
                Setting(name="platen_url", value="http://platen.test/intake/jobs", changed_by="leighton",
                        changed_at=now)])
    db.commit()
    clock[0] += 4.9
    assert worker.poll_seconds() == 5.0
    clock[0] += 0.2
    assert worker.poll_seconds() == 2.0 and worker._printing_set_up()


def test_values_on_screen_that_no_longer_fit_env_are_set_aside(db, caplog):
    db.add(Setting(name="eta_minutes_per_stop", value="0", changed_by="leighton",
                   changed_at=datetime.now(UTC)))
    db.commit()
    config.forget_settings()
    with caplog.at_level(logging.WARNING, logger="tms.settings"):
        assert get_settings().eta_minutes_per_stop == 20
    assert "don't fit .env any more" in caplog.text


def test_a_database_that_cant_be_read_leaves_the_settings_in_force(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def down():
        raise OperationalError("select", {}, Exception("no database"))

    monkeypatch.setattr("tms.db.get_engine", down)
    config.forget_settings()
    assert get_settings() is config.file_settings()
