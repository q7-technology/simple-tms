import pytest
from pydantic import ValidationError

from tms.config import Settings


@pytest.mark.parametrize("key", ["", "short", "x" * 31, "change-me",
                                 "change-me-to-a-long-random-string",
                                 "change-me-0123456789abcdef0123456789abcdef"])
def test_a_weak_secret_key_refuses_to_start(key):
    with pytest.raises(ValidationError, match=r"openssl rand -hex 32"):
        Settings(secret_key=key)


def test_the_default_secret_key_refuses_to_start(monkeypatch):
    monkeypatch.delenv("TMS_SECRET_KEY")
    with pytest.raises(ValidationError, match=r"TMS_SECRET_KEY"):
        Settings()


def test_a_long_random_secret_key_is_fine():
    key = "3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60"
    assert Settings(secret_key=key).secret_key == key


def test_printing_and_the_worker_start_switched_off_with_safe_waits():
    s = Settings(secret_key="3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60")
    assert (s.platen_url, s.platen_key, s.run_sheet_printer) == ("", "", "dispatch")
    assert s.worker_poll_seconds == 5.0 and s.worker_http_timeout_seconds == 10.0


def test_proof_of_delivery_is_kept_in_the_data_volume():
    assert Settings(secret_key="3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60").pod_dir == "/data/pod"


KEY = "3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60"


def test_positions_are_kept_90_days_and_no_map_tiles_are_set_up():
    s = Settings(secret_key=KEY)
    assert s.position_retention_days == 90
    assert (s.map_tile_url, s.map_attribution) == ("", "")
    assert s.eta_minutes_per_stop == 20


@pytest.mark.parametrize("url, origin", [
    ("https://api.maptiler.com/maps/streets-v2/256/{z}/{x}/{y}.png?key=abc",
     "https://api.maptiler.com"),
    ("https://tiles.example.com:8443/{z}/{x}/{y}.png", "https://tiles.example.com:8443"),
    ("/tiles/{z}/{x}/{y}.png", "")])
def test_a_tile_url_from_a_provider_or_this_server(url, origin):
    s = Settings(secret_key=KEY, map_tile_url=url, map_tile_origin=origin)
    assert (s.map_tile_url, s.map_tile_origin) == (url, origin)


@pytest.mark.parametrize("url", [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
    "http://tiles.example.com/{z}/{x}/{y}.png",
    "https://tiles.example.com/{z}/{x}.png",
    # Leaflet's subdomains: one origin for the CSP can't cover them
    "https://{s}.tiles.example.com/{z}/{x}/{y}.png"])
def test_a_tile_url_that_cant_be_used_refuses_to_start(url):
    with pytest.raises(ValidationError, match="TMS_MAP_TILE_URL"):
        Settings(secret_key=KEY, map_tile_url=url)


def test_a_retention_period_of_no_days_refuses_to_start():
    with pytest.raises(ValidationError):
        Settings(secret_key=KEY, position_retention_days=0)


@pytest.mark.parametrize("url, origin", [
    # it goes into the desktop's Content-Security-Policy as it is
    ("https://tiles.example.com/{z}/{x}/{y}.png", "https://tiles.example.com; script-src *"),
    ("https://tiles.example.com/{z}/{x}/{y}.png", "https://tiles.example.com 'unsafe-inline'"),
    ("https://tiles.example.com/{z}/{x}/{y}.png", "'self'"),
    ("https://tiles.example.com/{z}/{x}/{y}.png", "https://tiles.example.com/"),
    ("https://tiles.example.com/{z}/{x}/{y}.png", "http://tiles.example.com"),
    # it must be where the tiles come from
    ("https://tiles.example.com/{z}/{x}/{y}.png", ""),
    ("https://tiles.example.com/{z}/{x}/{y}.png", "https://other.example.com"),
    ("/tiles/{z}/{x}/{y}.png", "https://tiles.example.com"),
    ("", "https://tiles.example.com")])
def test_a_tile_origin_that_isnt_the_tiles_own_refuses_to_start(url, origin):
    with pytest.raises(ValidationError, match="TMS_MAP_TILE_ORIGIN"):
        Settings(secret_key=KEY, map_tile_url=url, map_tile_origin=origin)


def test_manifests_go_to_the_run_sheet_printer_unless_told():
    s = Settings(secret_key=KEY, run_sheet_printer="office")
    assert s.manifest_printer == "" and s.manifest_printer_or_default == "office"
    s = Settings(secret_key=KEY, run_sheet_printer="office", manifest_printer="bay")
    assert s.manifest_printer_or_default == "bay"


@pytest.mark.parametrize("field", ["wms_url", "platen_url"])
@pytest.mark.parametrize("url", ["wms.example.com", "ftp://wms.example.com", "https://"])
def test_an_address_that_isnt_http_refuses_to_start(field, url):
    with pytest.raises(ValidationError, match=f"TMS_{field.upper()} must be an http:// or https:// address"):
        Settings(secret_key=KEY, **{field: url})


def test_an_address_is_kept_without_spaces_around_it():
    assert Settings(secret_key=KEY, wms_url=" http://wms.test/ ").wms_url == "http://wms.test/"


@pytest.mark.parametrize("field, value", [("worker_poll_seconds", 0), ("worker_poll_seconds", 301),
                                          ("worker_http_timeout_seconds", 0), ("wms_timeout_seconds", 61)])
def test_a_wait_out_of_range_refuses_to_start(field, value):
    with pytest.raises(ValidationError, match=field):
        Settings(secret_key=KEY, **{field: value})


def test_a_setting_left_empty_in_env_is_its_default(monkeypatch):
    """Compose passes every TMS_* setting to the api and the worker, as
    `${TMS_X:-}`: one left out of .env comes through as "", and must mean
    config.py's default, not a value that can't be read."""
    for name in ("WMS_TIMEOUT_SECONDS", "WORKER_POLL_SECONDS", "WORKER_HTTP_TIMEOUT_SECONDS",
                 "POSITION_RETENTION_DAYS", "ETA_MINUTES_PER_STOP", "RUN_SHEET_PRINTER",
                 "POD_DIR", "PLATEN_KEY", "MANIFEST_PRINTER"):
        monkeypatch.setenv(f"TMS_{name}", "")
    s = Settings(secret_key=KEY)
    assert (s.wms_timeout_seconds, s.worker_poll_seconds, s.worker_http_timeout_seconds) == (
        5.0, 5.0, 10.0)
    assert (s.position_retention_days, s.eta_minutes_per_stop) == (90, 20)
    assert (s.run_sheet_printer, s.pod_dir) == ("dispatch", "/data/pod")
    assert (s.platen_key, s.manifest_printer) == ("", "")


def test_an_empty_secret_key_in_env_still_refuses_to_start(monkeypatch):
    monkeypatch.setenv("TMS_SECRET_KEY", "")
    with pytest.raises(ValidationError, match=r"TMS_SECRET_KEY"):
        Settings()


def test_compose_gives_the_api_and_the_worker_every_setting():
    """The Settings drawer shows what the api sees, and the worker reads them
    too: a setting Compose leaves out of one of them is one .env can't set."""
    import pathlib

    import yaml

    compose = yaml.safe_load(
        (pathlib.Path(__file__).parents[2] / "docker-compose.yml").read_text())
    wanted = {f"TMS_{name.upper()}" for name in Settings.model_fields}
    for service in ("api", "worker"):
        given = {n for n in compose["services"][service]["environment"] if n.startswith("TMS_")}
        assert given == wanted, service
