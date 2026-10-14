"""Positions are personal information, not business history: once older than
the retention period (90 days unless TMS_POSITION_RETENTION_DAYS says
otherwise) the worker deletes them, and clears the position on a proof of
delivery. Nothing else about a delivery changes."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update

from deliveries_samples import delivered, on_the_road
from tms.models import ConsignmentEvent, Pod, Position
from tms.worker import forget_positions

HERE = {"latitude": "-37.562210", "longitude": "143.850300", "accuracy_m": "12.0"}


def _position(db, run_id, days_ago: float):
    at = datetime.now(UTC) - timedelta(days=days_ago)
    db.add(Position(run_id=run_id, driver_id=1, vehicle_id=1, latitude=-37.5, longitude=143.8,
                    accuracy_m=10, recorded_at=at, received_at=at))
    db.commit()


def _backdate(db, pod_id, days):
    """A delivery made this long ago. A proof of delivery is kept as it was
    (the database refuses any other change), so the test steps round that."""
    db.execute(text("alter table pod disable trigger pod_kept"))
    db.execute(update(Pod).where(Pod.id == pod_id).values(at=Pod.at - timedelta(days=days)))
    db.execute(text("alter table pod enable trigger pod_kept"))
    db.commit()


def test_what_is_older_than_90_days_is_forgotten(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    _position(db, run["id"], 91)
    _position(db, run["id"], 89)
    delivered(client, kim, run["id"], a, position=HERE)
    delivered(client, kim, run["id"], b, position=HERE)
    old, recent = db.execute(select(Pod).order_by(Pod.id)).scalars().all()
    _backdate(db, old.id, days=91)
    history = db.execute(select(ConsignmentEvent.id, ConsignmentEvent.detail)).all()

    assert forget_positions(db) == (1, 1)

    left = db.execute(select(Position.recorded_at)).scalars().all()
    assert len(left) == 1 and left[0] > datetime.now(UTC) - timedelta(days=90)
    db.expire_all()
    old, recent = db.get(Pod, old.id), db.get(Pod, recent.id)
    assert (old.latitude, old.longitude, old.accuracy_m) == (None, None, None)
    assert old.signed_name == "Priya" and old.outcome == "delivered"
    assert str(recent.latitude) == "-37.562210"
    # the history was never told where anyone was, and is as it was
    assert db.execute(select(ConsignmentEvent.id, ConsignmentEvent.detail)).all() == history
    # once forgotten, there is nothing more to do
    assert forget_positions(db) == (0, 0)


def test_the_period_is_the_businesss_to_set(client, db, dispatcher_headers, monkeypatch):
    from tms.config import get_settings

    run, _, _ = on_the_road(client, db, dispatcher_headers)
    _position(db, run["id"], 31)
    _position(db, run["id"], 29)
    monkeypatch.setattr(get_settings(), "position_retention_days", 30)
    assert forget_positions(db) == (1, 0)


def test_it_is_time_to_forget_on_start_and_then_once_an_hour():
    from tms.worker import FORGET_EVERY_SECONDS, time_to_forget

    assert time_to_forget(None, 5.0)
    assert not time_to_forget(100.0, 100.0 + FORGET_EVERY_SECONDS - 1)
    assert time_to_forget(100.0, 100.0 + FORGET_EVERY_SECONDS)


def test_each_go_says_what_it_forgot_even_nothing(db, caplog):
    import logging

    from tms.worker import forget_positions

    with caplog.at_level(logging.INFO, logger="tms.worker"):
        assert forget_positions(db) == (0, 0)
    assert "forgot 0 positions, and where 0 deliveries were, older than 90 days" in caplog.text


def test_a_proof_of_delivery_is_kept_as_it_was_but_where_it_was_can_be_cleared(
        client, db, dispatcher_headers):
    import pytest
    from sqlalchemy import delete
    from sqlalchemy.exc import DBAPIError

    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a, position=HERE)
    pod = db.execute(select(Pod)).scalar_one()
    for change in ({"signed_name": "Someone else"}, {"latitude": -37.1},
                   {"latitude": None, "signed_name": "Someone else"}, {"at": Pod.at - timedelta(days=1)}):
        with pytest.raises(DBAPIError, match="kept as it was"):
            db.execute(update(Pod).where(Pod.id == pod.id).values(**change))
        db.rollback()
    with pytest.raises(DBAPIError, match="kept as it was"):
        db.execute(delete(Pod).where(Pod.id == pod.id))
    db.rollback()
    # the retention period's clearing is the one change allowed
    db.execute(update(Pod).where(Pod.id == pod.id)
               .values(latitude=None, longitude=None, accuracy_m=None))
    db.commit()
    db.expire_all()
    pod = db.get(Pod, pod.id)
    assert (pod.latitude, pod.longitude, pod.accuracy_m, pod.signed_name) == (None, None, None, "Priya")
