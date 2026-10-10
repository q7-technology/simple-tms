from conftest import PASSWORD, bearer, login


def test_sign_in_gives_a_token_that_says_who_you_are(client, dispatcher):
    session = login(client, "dee")
    assert session["token"].startswith("tms_s.")
    assert session["refresh_token"].startswith("tms_r.")
    assert session["user"] == {"id": dispatcher.id, "username": "dee",
                               "display_name": "Dee", "role": "dispatcher"}

    r = client.get("/v1/auth/me", headers=bearer(session["token"]))
    assert r.status_code == 200
    assert r.json()["kind"] == "user"
    assert r.json()["scopes"] == ["fleet:read", "consignments:read"]


def test_a_wrong_password_says_how_many_tries_are_left(client, dispatcher):
    r = client.post("/v1/auth/login", json={"username": "dee", "password": "nope"})
    assert r.status_code == 401
    assert r.json() == {"detail": "wrong username or password", "code": "wrong_password",
                        "tries_left": 4}


def test_an_unknown_user_looks_like_a_wrong_password(client):
    r = client.post("/v1/auth/login", json={"username": "nobody", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["code"] == "wrong_password"
    assert "tries_left" not in r.json()


def test_five_wrong_passwords_lock_the_account(client, dispatcher):
    for _ in range(4):
        client.post("/v1/auth/login", json={"username": "dee", "password": "nope"})
    r = client.post("/v1/auth/login", json={"username": "dee", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["code"] == "locked"
    # even the right password waits out the lock
    r = client.post("/v1/auth/login", json={"username": "dee", "password": PASSWORD})
    assert r.status_code == 401
    assert r.json()["code"] == "locked"


def test_refresh_hands_out_a_new_pair_and_spends_the_old_one(client, dispatcher):
    session = login(client, "dee")
    r = client.post("/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["refresh_token"] != session["refresh_token"]
    again = client.post("/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert again.status_code == 401


def test_logout_ends_the_session(client, dispatcher):
    session = login(client, "dee")
    assert client.post("/v1/auth/logout",
                       json={"refresh_token": session["refresh_token"]}).status_code == 204
    r = client.post("/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert r.status_code == 401


def test_no_token_or_a_bad_one_is_401(client):
    assert client.get("/v1/auth/me").status_code == 401
    assert client.get("/v1/auth/me", headers=bearer("tms_s.forged.sig")).status_code == 401
    assert client.get("/v1/auth/me", headers=bearer("wms_somebody-elses-key")).status_code == 401


def test_a_deactivated_user_loses_their_session(client, db, dispatcher):
    token = login(client, "dee")["token"]
    dispatcher.active = False
    db.commit()
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 401


def test_wrong_passwords_sent_all_at_once_still_lock_after_five(client, db, dispatcher):
    from conftest import all_at_once, fresh_client

    def guess():
        return fresh_client().post("/v1/auth/login",
                                   json={"username": "dee", "password": "nope"}).json()["code"]

    codes = all_at_once(10, guess)
    # each guess is weighed one after another: four are told how many tries are left,
    # the fifth locks the account, and the rest find it locked
    assert codes.count("wrong_password") == 4
    assert codes.count("locked") == 6
    db.refresh(dispatcher)
    assert dispatcher.locked_until is not None


def test_a_new_password_ends_every_session_signed_in_with_the_old_one(client, db, dispatcher):
    from tms.services import sessions

    old = login(client, "dee")
    sessions.change_password(db, dispatcher, "a brand new password")
    db.commit()

    assert client.get("/v1/auth/me", headers=bearer(old["token"])).status_code == 401
    r = client.post("/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})
    assert r.status_code == 401

    new = login(client, "dee", "a brand new password")
    assert client.get("/v1/auth/me", headers=bearer(new["token"])).status_code == 200
