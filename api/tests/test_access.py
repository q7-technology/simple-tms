from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tms.services import access, sessions


def test_a_password_hash_checks_the_password_and_nothing_else():
    stored = access.hash_password("correct horse battery")
    assert stored.startswith("pbkdf2_sha256$")
    assert access.verify_password("correct horse battery", stored)
    assert not access.verify_password("Correct horse battery", stored)
    assert not access.verify_password("anything", None)
    assert not access.verify_password("anything", "not-a-hash")


def test_an_access_token_names_its_user_until_it_expires():
    user = SimpleNamespace(id=42, password_hash="pbkdf2_sha256$1$00$00")
    now = datetime.now(UTC)
    token = sessions.issue_access_token(user, now=now)
    assert token.startswith("tms_s.")
    assert sessions.read_access_token(token, now=now) == 42
    later = now + timedelta(seconds=sessions.ACCESS_TTL_SECONDS + 1)
    assert sessions.read_access_token(token, now=later) is None


def test_a_tampered_token_is_refused():
    token = sessions.issue_access_token(SimpleNamespace(id=42, password_hash=None))
    payload, sig = token[len("tms_s."):].split(".", 1)
    assert sessions.read_access_token(f"tms_s.{payload}.{sig[:-2]}xx") is None
    assert sessions.read_access_token("tms_s.nonsense") is None


def test_a_driver_token_is_not_a_user_token():
    token = sessions.issue_driver_token(SimpleNamespace(id=7, pin_hash=None))
    assert token.startswith("tms_d.")
    assert sessions.read_driver_token(token) == 7
    assert sessions.read_access_token(token) is None


@pytest.mark.parametrize("stored", [
    "pbkdf2_sha256$600000$not-hex$" + "0" * 64,        # salt is not hex
    "pbkdf2_sha256$many$" + "0" * 32 + "$" + "0" * 64,  # iterations not a number
    "pbkdf2_sha256$0$" + "0" * 32 + "$" + "0" * 64,     # no iterations at all
    "pbkdf2_sha256$-5$" + "0" * 32 + "$" + "0" * 64,
    "pbkdf2_sha256$600000$" + "0" * 32 + "$",           # no digest
    "$$$",
    "not a hash",
])
def test_a_malformed_stored_hash_is_a_wrong_password_not_a_crash(stored):
    assert access.verify_password("anything", stored) is False
