import json
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from conftest import WEBHOOK_SECRET, beaten_to_it
from tms.models import Consignment, InboundEvent
from tms.services.signature import sign
from wms_samples import body_of, delivery, packed_event, post_event


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_delivery_packed_makes_a_consignment_with_its_packages(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"

    c = db.execute(select(Consignment)).scalar_one()
    assert r.json()["consignment_id"] == c.id
    assert (c.wms_ref, c.owner, c.warehouse, c.status) == ("0080012345", "DEFAULT", "BAL-WH01", "packed")
    assert c.customer == "Ballarat Hardware"
    assert c.ship_to["postcode"] == "3350"
    assert str(c.required_by) == "2026-10-11"
    assert [p.package_no for p in c.packages] == [1, 2]
    assert c.packages[0].weight_kg == Decimal("9.5")
    assert c.packages[0].sscc == "093123450000000012"
    assert c.packages[1].weight_kg is None
    assert [(e.kind, e.detail["wms_event_id"]) for e in c.events] == [("packed", event["event_id"])]
    assert db.get(InboundEvent, uuid.UUID(event["event_id"])).outcome == "created"

    # it asked the WMS for the delivery, with our key and the event's owner
    assert wms.calls == [{"path": "/v1/deliveries/0080012345", "query": "owner=DEFAULT",
                          "authorization": "Bearer wms_test-key-for-the-tms"}]


def test_a_repeated_event_id_does_nothing(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    assert post_event(client, event, WEBHOOK_SECRET).json()["status"] == "created"
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200
    assert r.json()["status"] == "duplicate"
    assert count(db, Consignment) == 1
    assert len(wms.calls) == 1


def test_a_second_event_for_the_same_delivery_does_not_make_a_second_consignment(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    post_event(client, packed_event(), WEBHOOK_SECRET)
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert count(db, Consignment) == 1


def test_the_same_event_arriving_twice_at_once_makes_one_consignment(client, db, engine, wms):
    """The other request records the event id just before this one does."""
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()

    def rival(obj):
        if isinstance(obj, InboundEvent):
            return InboundEvent(event_id=obj.event_id, event_type=obj.event_type,
                                external_ref=obj.external_ref, outcome="created")
        return None

    with beaten_to_it(engine, rival):
        r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "duplicate"
    assert count(db, Consignment) == 0  # the rival in this test only wrote the event id
    assert count(db, InboundEvent) == 1


def test_two_events_for_one_delivery_at_once_make_one_consignment(client, db, engine, wms):
    """Another event for the same delivery makes its consignment just before this one."""
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()

    def rival(obj):
        if isinstance(obj, Consignment):
            return Consignment(wms_ref=obj.wms_ref, owner=obj.owner, warehouse=obj.warehouse,
                               customer=obj.customer, ship_to={}, status="packed")
        return None

    with beaten_to_it(engine, rival):
        r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ignored"
    rival_id = db.execute(select(Consignment.id)).scalar_one()
    assert r.json()["consignment_id"] == rival_id
    assert db.get(InboundEvent, uuid.UUID(event["event_id"])).outcome == "ignored"


@pytest.mark.parametrize("signature", [None, "", "sha256=" + "0" * 64, "not even close"])
def test_a_missing_or_wrong_signature_is_401_and_does_nothing(client, db, wms, signature):
    wms.deliveries["0080012345"] = delivery()
    r = post_event(client, packed_event(), WEBHOOK_SECRET, signature=signature)
    assert r.status_code == 401
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0
    assert wms.calls == []


def test_signed_with_another_secret_is_401(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    r = post_event(client, packed_event(), "somebody-elses-secret-0123456789")
    assert r.status_code == 401
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0
    assert wms.calls == []


def test_an_event_we_do_not_act_on_is_2xx_logged_and_remembered(client, db, wms, caplog):
    event = {**packed_event(), "event_type": "delivery.picked",
             "data": {"delivery_ref": "0080012345", "complete": True, "lines": []}}
    with caplog.at_level(logging.INFO, logger="tms.wms"):
        r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert "delivery.picked" in caplog.text
    assert count(db, Consignment) == 0
    assert db.get(InboundEvent, uuid.UUID(event["event_id"])).outcome == "ignored"
    assert wms.calls == []
    # and a repeat of it is a duplicate
    assert post_event(client, event, WEBHOOK_SECRET).json()["status"] == "duplicate"


def test_wms_unreachable_is_503_and_nothing_is_kept_so_the_retry_works(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    wms.statuses.append(503)
    event = packed_event()
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 503
    assert r.json()["code"] == "wms_unavailable"
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0

    # the WMS worker sends the same event again a minute later
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "created"


def test_nobody_listening_at_the_wms_is_503_and_nothing_is_kept(client, db, monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_url", "http://127.0.0.1:9")
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 503 and r.json()["code"] == "wms_unavailable"
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0


def test_a_delivery_the_wms_will_not_show_us_is_503(client, db, wms):
    r = post_event(client, packed_event("NOT-THERE"), WEBHOOK_SECRET)
    assert r.status_code == 503
    assert "404" in r.json()["detail"]
    assert count(db, InboundEvent) == 0


def test_no_secret_configured_is_503(client, db, wms, monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_webhook_secret", "")
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    assert count(db, InboundEvent) == 0


def test_a_signed_body_that_is_not_an_event_is_422(client, db, wms):
    body = b'{"hello":"world"}'
    r = client.post("/v1/wms/events", content=body,
                    headers={"X-WMS-Signature": sign(WEBHOOK_SECRET, body)})
    assert r.status_code == 422


def test_a_signed_body_that_is_not_json_is_422(client, db, wms):
    body = b"not json"
    r = client.post("/v1/wms/events", content=body,
                    headers={"X-WMS-Signature": sign(WEBHOOK_SECRET, body)})
    assert r.status_code == 422


def test_a_packed_event_with_bad_packages_is_422_and_nothing_is_kept(client, db, wms):
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    event["data"]["packages"][0]["weight_kg"] = "heavy"
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 422
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0


def test_the_signature_is_over_the_exact_bytes(client, db, wms):
    """Re-serialising the JSON would change the bytes; we check what arrived."""
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    pretty = json.dumps(event, indent=2).encode()
    r = client.post("/v1/wms/events", content=pretty,
                    headers={"X-WMS-Signature": sign(WEBHOOK_SECRET, body_of(event))})
    assert r.status_code == 401
    # and the same pretty bytes, signed as they are, are fine
    r = client.post("/v1/wms/events", content=pretty,
                    headers={"X-WMS-Signature": sign(WEBHOOK_SECRET, pretty)})
    assert r.status_code == 200 and r.json()["status"] == "created"


def test_decimals_never_pass_through_a_float(client, db, wms):
    """12 digits before the point and 6 after is more than a float holds."""
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    event["data"]["packages"][0]["weight_kg"] = "123456789012.123456"
    body = body_of(event)
    # a WMS that sent a bare JSON number would be read exactly too
    body = body.replace(b'"length_cm":"60"', b'"length_cm":123456789012.123456')
    r = client.post("/v1/wms/events", content=body,
                    headers={"X-WMS-Signature": sign(WEBHOOK_SECRET, body)})
    assert r.status_code == 200, r.text
    p = db.execute(select(Consignment)).scalar_one().packages[0]
    assert p.weight_kg == Decimal("123456789012.123456")
    assert p.length_cm == Decimal("123456789012.123456")


def test_no_transaction_is_held_while_simple_wms_is_asked(client, db, engine, monkeypatch):
    """The WMS may take seconds to answer; no row lock or snapshot waits on it."""
    from sqlalchemy import text

    from tms.services import wms_client

    held = []

    def get_delivery(ref, owner):
        with engine.connect() as conn:
            held.extend(conn.execute(text(
                "select pid, query from pg_stat_activity where datname = current_database() "
                "and state like 'idle in transaction%' and pid <> pg_backend_pid()")).all())
        return delivery(ref)

    monkeypatch.setattr(wms_client, "get_delivery", get_delivery)
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "created", r.text
    assert held == []


def _with(d: dict, **changes) -> dict:
    return {**d, **changes}


@pytest.mark.parametrize("bad, field", [
    (_with(delivery(), ship_to="12 Sturt St, Ballarat"), "delivery.ship_to"),
    (_with(delivery(), ship_to=None), "delivery.ship_to"),
    ({k: v for k, v in delivery().items() if k != "ship_to"}, "delivery.ship_to"),
    (_with(delivery(), ship_to={**delivery()["ship_to"], "name": 42}), "delivery.ship_to"),
    (_with(delivery(), ship_to={**delivery()["ship_to"], "name": {"first": "B"}}),
     "delivery.ship_to"),
    (_with(delivery(), required_by="next Tuesday"), "delivery.required_by"),
    (_with(delivery(), required_by="2026-13-45"), "delivery.required_by"),
    (_with(delivery(), warehouse="W" * 33), "delivery.warehouse"),
])
def test_a_delivery_simple_wms_answers_in_a_shape_we_cannot_read_is_422(
        client, db, wms, bad, field):
    """It will not read any better on a retry, so not a 503; nothing is kept and
    Simple WMS shows the event as failed, with our reason."""
    wms.deliveries["0080012345"] = bad
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 422, r.text
    errors = r.json()["errors"]
    assert [e["field"] for e in errors] == [field]
    assert "0080012345" in errors[0]["message"] and "Simple WMS" in errors[0]["message"]
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0


def test_a_long_customer_name_is_cut_to_200(client, db, wms):
    d = delivery()
    d["ship_to"]["name"] = "B" * 300
    wms.deliveries["0080012345"] = d
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    c = db.execute(select(Consignment)).scalar_one()
    assert c.customer == "B" * 200
    assert c.ship_to["name"] == "B" * 300  # the ship-to is kept as Simple WMS gave it


def test_a_delivery_with_no_required_by_is_fine(client, db, wms):
    wms.deliveries["0080012345"] = _with(delivery(), required_by=None)
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    assert db.execute(select(Consignment)).scalar_one().required_by is None


@pytest.mark.parametrize("key, value", [
    ("weight_kg", "9.1234567"),             # a 7th decimal place: refused, not rounded
    ("length_cm", "1234567890123.5"),        # 13 digits before the point: no room
    ("width_cm", "1E+20"),
    ("height_cm", "-1"),
    ("weight_kg", "NaN"),
    ("package_no", 2 ** 31),
    ("package_no", 0),
])
def test_package_numbers_that_do_not_fit_are_422_not_rounded_or_a_crash(
        client, db, wms, key, value):
    wms.deliveries["0080012345"] = delivery()
    event = packed_event()
    event["data"]["packages"][0][key] = value
    r = post_event(client, event, WEBHOOK_SECRET)
    assert r.status_code == 422, r.text
    assert [e["field"] for e in r.json()["errors"]] == [f"data.packages.0.{key}"]
    assert count(db, Consignment) == 0 and count(db, InboundEvent) == 0
    assert wms.calls == []


@pytest.mark.parametrize("secret", ["   ", "fifteen-chars-x", "  fifteen-chars-x  "])
def test_a_secret_simple_wms_would_not_take_is_not_configured(client, db, wms, monkeypatch,
                                                              secret):
    """Simple WMS takes 16 to 128 characters, stripped; anything shorter here is a
    mistake in .env, not a secret."""
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_webhook_secret", secret)
    wms.deliveries["0080012345"] = delivery()
    r = post_event(client, packed_event(), secret.strip())
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    assert count(db, InboundEvent) == 0 and wms.calls == []


def test_spaces_around_the_secret_are_not_part_of_it(client, db, wms, monkeypatch):
    """Simple WMS strips the secret it is given, so it signs without them."""
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_webhook_secret", f"  {WEBHOOK_SECRET}\n")
    wms.deliveries["0080012345"] = delivery()
    r = post_event(client, packed_event(), WEBHOOK_SECRET)
    assert r.status_code == 200 and r.json()["status"] == "created"
