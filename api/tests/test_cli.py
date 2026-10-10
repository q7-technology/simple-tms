from sqlalchemy import select

from tms.cli import main
from tms.models import User
from tms.services.access import verify_password


def test_create_user_makes_someone_who_can_sign_in(client, db, capsys):
    assert main(["create-user", "--username", "kim", "--role", "manager",
                 "--password", "a long enough password"]) == 0
    assert "user kim" in capsys.readouterr().out
    user = db.execute(select(User).where(User.username == "kim")).scalar_one()
    assert user.role == "manager"
    assert verify_password("a long enough password", user.password_hash)
    r = client.post("/v1/auth/login", json={"username": "kim", "password": "a long enough password"})
    assert r.status_code == 200


def test_create_user_refuses_a_short_password(db, capsys):
    assert main(["create-user", "--username", "kim", "--password", "short"]) == 1
    assert "at least 12" in capsys.readouterr().err
    assert db.execute(select(User)).first() is None


def test_create_user_refuses_a_taken_username(db, admin, capsys):
    assert main(["create-user", "--username", admin.username,
                 "--password", "a long enough password"]) == 1
    assert "already exists" in capsys.readouterr().err
