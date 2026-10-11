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
