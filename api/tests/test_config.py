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


@pytest.mark.parametrize("url", [
    "https://api.maptiler.com/maps/streets-v2/256/{z}/{x}/{y}.png?key=abc",
    "/tiles/{z}/{x}/{y}.png"])
def test_a_tile_url_from_a_provider_or_this_server(url):
    assert Settings(secret_key=KEY, map_tile_url=url).map_tile_url == url


@pytest.mark.parametrize("url", [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
    "http://tiles.example.com/{z}/{x}/{y}.png",
    "https://tiles.example.com/{z}/{x}.png"])
def test_a_tile_url_that_cant_be_used_refuses_to_start(url):
    with pytest.raises(ValidationError, match="TMS_MAP_TILE_URL"):
        Settings(secret_key=KEY, map_tile_url=url)


def test_a_retention_period_of_no_days_refuses_to_start():
    with pytest.raises(ValidationError):
        Settings(secret_key=KEY, position_retention_days=0)
