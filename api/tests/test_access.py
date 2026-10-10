from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tms.services import access, sessions


def test_a_password_hash_checks_the_password_and_nothing_else():
    stored = access.hash_password("correct horse battery")
    assert stored.startswith("pbkdf2_sha256$")
    assert access.verify_password("correct horse battery", stored)
    assert not access.verify_password("Correct horse battery", stored)
    assert not access.verify_password("anything", None)
    assert not access.verify_password("anything", "not-a-hash")


def test_an_access_token_names_its_user_until_it_expires():
    user = SimpleNamespace(id=42)
    now = datetime.now(UTC)
    token = sessions.issue_access_token(user, now=now)
    assert token.startswith("tms_s.")
    assert sessions.read_access_token(token, now=now) == 42
    later = now + timedelta(seconds=sessions.ACCESS_TTL_SECONDS + 1)
    assert sessions.read_access_token(token, now=later) is None


def test_a_tampered_token_is_refused():
    token = sessions.issue_access_token(SimpleNamespace(id=42))
    payload, sig = token[len("tms_s."):].split(".", 1)
    assert sessions.read_access_token(f"tms_s.{payload}.{sig[:-2]}xx") is None
    assert sessions.read_access_token("tms_s.nonsense") is None


def test_a_driver_token_is_not_a_user_token():
    token = sessions.issue_driver_token(7)
    assert token.startswith("tms_d.")
    assert sessions.read_driver_token(token) == 7
    assert sessions.read_access_token(token) is None
