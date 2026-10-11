"""A key for a system: Platen saying a run sheet printed."""
from conftest import bearer
from tms.cli import main


def _key(capsys, name="platen", scopes="printing:write") -> str:
    assert main(["create-api-key", "--name", name, "--scopes", scopes]) == 0
    out = capsys.readouterr().out
    assert f"api key {name}" in out and "shown once" in out
    return out.strip().splitlines()[-1]


def test_a_key_is_shown_once_and_says_who_it_is(client, db, capsys):
    raw = _key(capsys)
    assert raw.startswith("tms_k.")
    me = client.get("/v1/auth/me", headers=bearer(raw)).json()
    assert me == {"kind": "key", "id": me["id"], "name": "platen", "display_name": "platen",
                  "role": "key", "scopes": ["printing:write"]}


def test_a_key_does_only_what_its_scopes_say(client, db, capsys):
    raw = _key(capsys)
    assert client.get("/v1/runs", params={"date": "2026-10-14"},
                      headers=bearer(raw)).status_code == 403


def test_a_revoked_or_made_up_key_is_refused(client, db, capsys):
    raw = _key(capsys)
    assert main(["revoke-api-key", "--name", "platen"]) == 0
    assert client.get("/v1/auth/me", headers=bearer(raw)).status_code == 401
    assert client.get("/v1/auth/me", headers=bearer("tms_k.made-up")).status_code == 401


def test_a_key_needs_known_scopes_and_its_own_name(db, capsys):
    assert main(["create-api-key", "--name", "x", "--scopes", "everything"]) == 1
    assert "scopes must be some of" in capsys.readouterr().err
    _key(capsys)
    assert main(["create-api-key", "--name", "platen", "--scopes", "printing:write"]) == 1
    assert "already exists" in capsys.readouterr().err
