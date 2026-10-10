"""A consignment's history is append-only, and the database says so."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from tms.models import Consignment, ConsignmentEvent


@pytest.fixture
def consignment(db):
    c = Consignment(wms_ref="0080012345", warehouse="BAL-WH01", owner="DEFAULT",
                    customer="Ballarat Hardware", ship_to={"name": "Ballarat Hardware"},
                    status="packed")
    c.events.append(ConsignmentEvent(kind="packed", at=datetime.now(UTC), detail={}))
    db.add(c)
    db.commit()
    return c


def test_history_rows_cannot_be_updated(db, consignment):
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(update(ConsignmentEvent).values(kind="delivered"))
        db.commit()


def test_history_rows_cannot_be_deleted(db, consignment):
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(delete(ConsignmentEvent))
        db.commit()


def test_a_correction_is_a_new_row(db, consignment):
    db.add(ConsignmentEvent(consignment_id=consignment.id, kind="packed",
                            at=datetime.now(UTC), detail={"note": "recounted: 3 cartons, not 2"}))
    db.commit()
    db.refresh(consignment)
    assert [e.kind for e in consignment.events] == ["packed", "packed"]
