"""carrier names one whatever their case, in the database too; and con notes
found quickly among bookings taken off the bay, which keep theirs for good

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    clashes = op.get_bind().execute(sa.text(
        "select string_agg(name, ' and ' order by name) from carrier "
        "group by lower(name) having count(*) > 1")).scalars().all()
    if clashes:
        raise RuntimeError(
            "carrier names that differ only by case: " + "; ".join(clashes)
            + ". A carrier's name is one whatever its case, so rename all but one of each "
            "(PATCH /v1/carriers/{id} with a new name), then upgrade again.")
    op.create_index("uq_carrier_name_lower", "carrier", [sa.text("lower(name)")], unique=True)
    op.create_index("ix_booking_carrier_id_con_note", "booking", ["carrier_id", "con_note"])


def downgrade() -> None:
    op.drop_index("ix_booking_carrier_id_con_note", table_name="booking")
    op.drop_index("uq_carrier_name_lower", table_name="carrier")
