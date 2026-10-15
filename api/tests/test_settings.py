"""Settings an admin changes on screen in place of .env's: which may be,
checked with config.py's own rules, used by the API and the worker without a
restart, put back to .env's, and secrets never shown or logged again."""
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from tms import config, worker
from tms.config import Settings, get_settings
from tms.models import Setting, SettingChange
from tms.services import runtime_settings

KEY = "plt_on-screen-0123456789"


def _patch(client, headers, body):
    return client.patch("/v1/settings", headers=headers, json=body)


def _one(settings: list[dict], name: str) -> dict:
    return next(s for s in settings if s["name"] == name)


def test_every_setting_has_a_kind_and_none_is_left_out():
    """A new TMS_* setting must be put in one of the three kinds."""
    named = set(runtime_settings.ON_SCREEN) | set(runtime_settings.SECRETS) | set(runtime_settings.FILE_ONLY)
    assert named == set(Settings.model_fields)
    assert not set(runtime_settings.ON_SCREEN) & set(runtime_settings.SECRETS)


def test_only_an_admin_reads_or_changes_settings(client, admin_headers, manager_headers,
                                                 dispatcher_headers):
    for headers in (manager_headers, dispatcher_headers):
        assert client.get("/v1/settings", headers=headers).status_code == 403
        assert _patch(client, headers, {"run_sheet_printer": "Office"}).status_code == 403
    assert client.get("/v1/settings", headers=admin_headers).status_code == 200


def test_each_is_listed_with_its_kind_and_no_secret_is_shown(client, admin_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "wms_api_key", "wms_from-env-key")
    out = client.get("/v1/settings", headers=admin_headers).json()
    assert {s["name"] for s in out} == set(Settings.model_fields)
    printer = _one(out, "run_sheet_printer")
    assert printer | {"changed_at": None} == {
        "name": "run_sheet_printer", "env": "TMS_RUN_SHEET_PRINTER", "kind": "screen",
        "value": "dispatch", "default": "dispatch", "is_set": True, "on_screen": False,
        "changed_by": None, "changed_at": None, "why_file_only": None}
    key = _one(out, "wms_api_key")
    assert (key["kind"], key["value"], key["default"], key["is_set"]) == ("secret", None, None, True)
    for name in ("database_url", "secret_key"):
        assert _one(out, name)["kind"] == "file" and _one(out, name)["value"] is None
    assert _one(out, "map_tile_origin")["why_file_only"].startswith("Caddy reads it")
    assert "wms_from-env-key" not in str(out) and get_settings().secret_key not in str(out)


@pytest.mark.parametrize("name, says", [
    ("database_url", "TMS_DATABASE_URL is set in .env only"),
    ("secret_key", "TMS_SECRET_KEY is set in .env only"),
    ("map_tile_url", "TMS_MAP_TILE_URL is set in .env only"),
    ("map_tile_origin", "TMS_MAP_TILE_ORIGIN is set in .env only"),
    ("pod_dir", "TMS_POD_DIR is set in .env only"),
    ("domain", "not a setting Simple TMS has"),
])
def test_a_file_only_setting_is_refused(client, db, admin_headers, name, says):
    r = _patch(client, admin_headers, {name: "https://elsewhere.example", "run_sheet_printer": "Office"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == name and says in r.json()["errors"][0]["message"]
    # and nothing else in the same request was kept
    assert db.execute(select(SettingChange)).first() is None
    assert get_settings().run_sheet_printer == "dispatch"


def test_a_value_given_on_screen_is_used_until_it_is_put_back_to_envs(client, admin_headers):
    r = _patch(client, admin_headers, {"run_sheet_printer": " Office 2 ", "eta_minutes_per_stop": "25"})
    assert r.status_code == 200, r.text
    printer = _one(r.json(), "run_sheet_printer")
    assert (printer["value"], printer["default"], printer["on_screen"], printer["changed_by"]) == (
        "Office 2", "dispatch", True, "leighton")
    assert get_settings().run_sheet_printer == "Office 2"
    assert get_settings().eta_minutes_per_stop == 25

    r = _patch(client, admin_headers, {"run_sheet_printer": None})
    assert r.status_code == 200
    assert _one(r.json(), "run_sheet_printer")["on_screen"] is False
    assert get_settings().run_sheet_printer == "dispatch"
    assert get_settings().eta_minutes_per_stop == 25

    history = client.get("/v1/settings/history", headers=admin_headers).json()
    assert [(h["name"], h["action"], h["value"], h["changed_by"]) for h in history] == [
        ("run_sheet_printer", "reset", None, "leighton"),
        ("eta_minutes_per_stop", "set", "25", "leighton"),
        ("run_sheet_printer", "set", "Office 2", "leighton")]


def test_values_are_checked_with_configs_own_rules_all_or_none(client, admin_headers):
    r = _patch(client, admin_headers, {"eta_minutes_per_stop": 0, "worker_poll_seconds": "soon",
                                       "wms_url": "ftp://wms.example", "manifest_printer": "Bay"})
    assert r.status_code == 422
    assert {e["field"]: e["message"] for e in r.json()["errors"]} == {
        "eta_minutes_per_stop": "Input should be greater than or equal to 1",
        "worker_poll_seconds": "Input should be a valid number, unable to parse string as a number",
        "wms_url": "TMS_WMS_URL must be an http:// or https:// address, or empty",
        # a new address needs its key with it, too
        "wms_api_key": "Type the Simple WMS key again with a new address: a key on file is only "
                       "sent where it was given for"}
    assert get_settings().manifest_printer == ""


def test_a_secret_is_replaced_but_never_shown_kept_in_the_record_or_logged(client, db, admin_headers,
                                                                           caplog):
    with caplog.at_level(logging.DEBUG):
        r = _patch(client, admin_headers, {"platen_key": f"  {KEY} "})
        assert r.status_code == 200, r.text
        key = _one(r.json(), "platen_key")
        assert (key["value"], key["is_set"], key["on_screen"]) == (None, True, True)
        assert KEY not in r.text
        assert KEY not in client.get("/v1/settings", headers=admin_headers).text
        history = client.get("/v1/settings/history", headers=admin_headers).json()
        assert history[0] | {"changed_at": None, "id": None} == {
            "id": None, "name": "platen_key", "env": "TMS_PLATEN_KEY", "action": "set",
            "value": None, "secret": True, "changed_by": "leighton", "changed_at": None}
    assert get_settings().platen_key == KEY
    assert KEY not in caplog.text
    assert db.execute(select(SettingChange.value)).scalars().all() == [None]


def test_a_secret_cant_be_blank_and_a_webhook_secret_is_as_long_as_simple_wmss(client, admin_headers):
    r = _patch(client, admin_headers, {"platen_key": " ", "wms_webhook_secret": "short"})
    assert {e["field"]: e["message"] for e in r.json()["errors"]} == {
        "platen_key": "Type the new value, or use .env's",
        "wms_webhook_secret": "16 to 128 characters, the same as Simple WMS's"}


def test_a_new_address_needs_its_key_given_with_it(client, admin_headers):
    r = _patch(client, admin_headers, {"platen_url": "https://platen.example/intake/jobs"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "platen_key"
    r = _patch(client, admin_headers, {"platen_url": "https://platen.example/intake/jobs",
                                       "platen_key": KEY})
    assert r.status_code == 200, r.text
    # the same address again needs nothing more, and neither does going back to .env's
    assert _patch(client, admin_headers, {"platen_url": "https://platen.example/intake/jobs"}).status_code == 200
    assert _patch(client, admin_headers, {"platen_url": None}).status_code == 200


PAIRS = [("wms_url", "wms_api_key", "Simple WMS"), ("platen_url", "platen_key", "Platen")]


def _history(client, headers):
    return [(h["name"], h["action"]) for h in client.get("/v1/settings/history", headers=headers).json()]


@pytest.mark.parametrize("address, key, says", PAIRS)
def test_a_key_isnt_put_back_to_envs_while_its_address_is_one_given_here(client, admin_headers,
                                                                        address, key, says):
    """.env's key would go to the address given on screen: refused."""
    env = config.file_settings()
    assert _patch(client, admin_headers, {address: "https://elsewhere.example/v1", key: KEY}).status_code == 200
    r = _patch(client, admin_headers, {key: None})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": key, "message":
        f"Reset the {says} address too, or give a new key: .env's key only goes to .env's address."}]
    assert (getattr(get_settings(), address), getattr(get_settings(), key)) == ("https://elsewhere.example/v1", KEY)

    # with the address in the same save, both are .env's
    assert _patch(client, admin_headers, {address: None, key: None}).status_code == 200
    assert (getattr(get_settings(), address), getattr(get_settings(), key)) == (
        getattr(env, address), getattr(env, key))


@pytest.mark.parametrize("address, key, says", PAIRS)
def test_an_address_put_back_to_envs_takes_its_key_with_it(client, admin_headers, address, key, says):
    """The key given here would go to .env's address: it goes back too, and
    the record says both."""
    env = config.file_settings()
    assert _patch(client, admin_headers, {address: "https://elsewhere.example/v1", key: KEY}).status_code == 200
    r = _patch(client, admin_headers, {address: None})
    assert r.status_code == 200, r.text
    assert (_one(r.json(), address)["on_screen"], _one(r.json(), key)["on_screen"]) == (False, False)
    assert (getattr(get_settings(), address), getattr(get_settings(), key)) == (
        getattr(env, address), getattr(env, key))
    assert _history(client, admin_headers)[:2] == [(key, "reset"), (address, "reset")]


@pytest.mark.parametrize("address, key, says", PAIRS)
def test_a_key_given_with_envs_address_is_kept_and_put_back_freely(client, admin_headers, address, key,
                                                                   says):
    """A key given here while .env's address is in force was given for it."""
    env = config.file_settings()
    # with the address put back in the same save: the new key is for .env's address
    assert _patch(client, admin_headers, {address: "https://elsewhere.example/v1", key: "first-key"}).status_code == 200
    assert _patch(client, admin_headers, {address: None, key: KEY}).status_code == 200
    assert (getattr(get_settings(), address), getattr(get_settings(), key)) == (getattr(env, address), KEY)
    # and with .env's address in force, the key alone goes back
    assert _patch(client, admin_headers, {key: None}).status_code == 200
    assert getattr(get_settings(), key) == getattr(env, key)


def test_a_key_goes_back_while_the_address_given_here_is_envs_own(client, admin_headers):
    env = config.file_settings()
    assert _patch(client, admin_headers, {"wms_url": env.wms_url, "wms_api_key": KEY}).status_code == 200
    assert _patch(client, admin_headers, {"wms_api_key": None}).status_code == 200
    assert get_settings().wms_api_key == env.wms_api_key


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


def test_settings_forgotten_by_another_thread_mid_read_are_still_returned():
    """A save in another thread forgets them (forget_settings) just as this
    one returns them: what it read is returned, never None."""
    import linecache
    import sys

    code = config.get_settings.__code__

    def tracer(frame, event, arg):
        if frame.f_code is not code:
            return None
        if event == "line" and linecache.getline(code.co_filename, frame.f_lineno).strip().startswith("return"):
            config.forget_settings()
        return tracer

    config.forget_settings()
    sys.settrace(tracer)
    try:
        got = get_settings()
    finally:
        sys.settrace(None)
    assert got is config.file_settings()
