from conftest import bearer

ALEX = {"code": "drv-01", "name": "Alex T.", "pin": "2468"}
# no tries_left: an unknown code gets the same answer, so it must not differ
WRONG_PIN = {"detail": "wrong driver code or PIN", "code": "wrong_pin"}


def make_driver(client, headers, body=ALEX):
    r = client.post("/v1/drivers", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_manager_adds_a_driver_and_the_pin_is_never_shown(client, manager_headers):
    out = make_driver(client, manager_headers)
    assert out["code"] == "drv-01" and out["name"] == "Alex T."
    assert out["active"] is True and out["locked"] is False
    assert "pin" not in out and "pin_hash" not in out


def test_a_dispatcher_can_see_drivers_but_not_change_them(client, manager_headers, dispatcher_headers):
    make_driver(client, manager_headers)
    r = client.get("/v1/drivers", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["total"] == 1
    r = client.post("/v1/drivers", headers=dispatcher_headers,
                    json={"code": "drv-02", "name": "Sam", "pin": "1357"})
    assert r.status_code == 403


def test_a_code_is_used_once(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.post("/v1/drivers", headers=manager_headers, json=ALEX)
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "code"


def test_a_pin_is_four_to_eight_digits(client, manager_headers):
    r = client.post("/v1/drivers", headers=manager_headers, json={**ALEX, "pin": "12a4"})
    assert r.status_code == 422


def test_update_changes_the_name_and_pin(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.patch("/v1/drivers/drv-01", headers=manager_headers,
                     json={"name": "Alex Tran", "pin": "8642"})
    assert r.status_code == 200 and r.json()["name"] == "Alex Tran"
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "8642"})
    assert r.status_code == 200


def test_deactivated_not_deleted(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.post("/v1/drivers/drv-01/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False
    assert client.get("/v1/drivers?active=false", headers=manager_headers).json()["total"] == 1
    assert client.get("/v1/drivers?active=true", headers=manager_headers).json()["total"] == 0
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.status_code == 401
    r = client.post("/v1/drivers/drv-01/reactivate", headers=manager_headers)
    assert r.json()["active"] is True
    assert client.delete("/v1/drivers/drv-01", headers=manager_headers).status_code == 405


def test_driver_signs_in_with_code_and_pin(client, manager_headers):
    make_driver(client, manager_headers)
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("tms_d.")
    me = client.get("/v1/auth/me", headers=bearer(token)).json()
    assert me["kind"] == "driver" and me["name"] == "drv-01"
    assert me["scopes"] == ["fleet:read", "deliveries:read", "deliveries:write"]
    # a driver may see the fleet, but not change it
    assert client.get("/v1/vehicles", headers=bearer(token)).status_code == 200
    r = client.post("/v1/drivers", headers=bearer(token),
                    json={"code": "drv-02", "name": "Sam", "pin": "1357"})
    assert r.status_code == 403


def test_five_wrong_pins_lock_the_driver_until_a_manager_unlocks(client, manager_headers):
    make_driver(client, manager_headers)
    for _ in range(4):
        r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
        assert r.status_code == 401 and r.json() == WRONG_PIN
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
    assert r.status_code == 401 and r.json()["code"] == "locked"
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.json()["code"] == "locked"

    r = client.post("/v1/drivers/drv-01/unlock", headers=manager_headers)
    assert r.json()["locked"] is False and r.json()["failed_attempts"] == 0
    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    assert r.status_code == 200


def test_a_right_pin_clears_the_count(client, manager_headers, db):
    make_driver(client, manager_headers)
    client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
    client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})
    r = client.get("/v1/drivers/drv-01", headers=manager_headers)
    assert r.json()["failed_attempts"] == 0


def test_wrong_pins_sent_all_at_once_still_lock_after_five(client, manager_headers, db):
    from conftest import all_at_once, fresh_client
    from sqlalchemy import select

    from tms.models import Driver

    make_driver(client, manager_headers)

    def guess():
        return fresh_client().post("/v1/auth/driver-login",
                                   json={"code": "drv-01", "pin": "0000"}).json()["code"]

    codes = all_at_once(10, guess)
    # only five PINs are ever checked: four are told the PIN is wrong,
    # the fifth locks the driver, and the rest find them locked
    assert codes.count("wrong_pin") == 4
    assert codes.count("locked") == 6
    driver = db.execute(select(Driver).where(Driver.code == "drv-01")).scalar_one()
    assert driver.locked is True and driver.failed_attempts == 5


def test_a_new_pin_ends_the_driver_sessions_signed_in_with_the_old_one(client, manager_headers):
    make_driver(client, manager_headers)
    old = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"}).json()["token"]
    assert client.get("/v1/auth/me", headers=bearer(old)).status_code == 200

    r = client.patch("/v1/drivers/drv-01", headers=manager_headers, json={"pin": "8642"})
    assert r.status_code == 200
    assert client.get("/v1/auth/me", headers=bearer(old)).status_code == 401

    new = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "8642"}).json()["token"]
    assert client.get("/v1/auth/me", headers=bearer(new)).status_code == 200


def test_a_new_name_keeps_the_driver_signed_in(client, manager_headers):
    make_driver(client, manager_headers)
    token = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"}).json()["token"]
    client.patch("/v1/drivers/drv-01", headers=manager_headers, json={"name": "Alex Tran"})
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 200


def test_a_code_taken_by_another_request_meanwhile_is_refused_not_a_crash(
        client, manager_headers, engine):
    from conftest import beaten_to_it
    from tms.models import Driver

    def rival(obj):
        if isinstance(obj, Driver):
            return Driver(code=obj.code, name="Someone else", pin_hash="x",
                          failed_attempts=0, locked=False, active=True)

    with beaten_to_it(engine, rival):
        r = client.post("/v1/drivers", headers=manager_headers, json=ALEX)
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "code", "message": "driver drv-01 already exists"}]


def test_an_unknown_or_inactive_code_looks_like_a_wrong_pin(client, manager_headers, monkeypatch):
    from tms.api.routes import auth
    from tms.services import access

    checked = []
    real = access.verify_password

    def spy(pin, stored):
        checked.append(stored)
        return real(pin, stored)

    make_driver(client, manager_headers)
    monkeypatch.setattr(auth.access, "verify_password", spy)

    wrong = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
    unknown = client.post("/v1/auth/driver-login", json={"code": "nobody", "pin": "0000"})
    client.post("/v1/drivers/drv-01/deactivate", headers=manager_headers)
    # even the right PIN, once the driver is deactivated
    inactive = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"})

    for r in (wrong, unknown, inactive):
        assert r.status_code == 401
        assert r.json() == WRONG_PIN
        assert r.headers.get("www-authenticate") == "Bearer"
    # and each took as long as a wrong PIN, against a hash with the same work
    assert checked[1:] == [access.DUMMY_HASH, access.DUMMY_HASH]


def test_a_code_is_kept_lower_case_and_is_one_code_whatever_the_case(client, manager_headers):
    out = make_driver(client, manager_headers, {**ALEX, "code": "KIM-07"})
    assert out["code"] == "kim-07"
    r = client.post("/v1/drivers", headers=manager_headers, json={**ALEX, "code": "Kim-07"})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "code", "message": "driver kim-07 already exists"}]


def test_a_code_in_the_address_is_read_whatever_the_case(client, manager_headers):
    make_driver(client, manager_headers)
    assert client.get("/v1/drivers/DRV-01", headers=manager_headers).json()["code"] == "drv-01"
    r = client.patch("/v1/drivers/Drv-01", headers=manager_headers, json={"name": "Alex Tran"})
    assert r.status_code == 200 and r.json()["name"] == "Alex Tran"


def test_a_driver_signs_in_whatever_the_case_of_the_code(client, manager_headers):
    make_driver(client, manager_headers)
    for typed in ("DRV-01", "Drv-01", " drv-01 "):
        r = client.post("/v1/auth/driver-login", json={"code": typed, "pin": "2468"})
        assert r.status_code == 200, typed
