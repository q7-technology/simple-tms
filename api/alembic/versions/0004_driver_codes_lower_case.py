"""driver codes kept lower case, so a code matches however it is typed

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    clashes = op.get_bind().execute(sa.text(
        "select string_agg(code, ' and ' order by code) from driver "
        "group by lower(code) having count(*) > 1")).scalars().all()
    if clashes:
        raise RuntimeError(
            "driver codes that differ only by case: " + "; ".join(clashes)
            + ". Codes are now one whatever the case, so rename all but one of each (in psql: "
            "update driver set code = '<new code>' where code = '<old code>'), then upgrade again.")
    op.execute("update driver set code = lower(code) where code <> lower(code)")
    op.create_check_constraint(op.f("ck_driver_code_lower"), "driver", "code = lower(code)")


def downgrade() -> None:
    # the codes stay lower case: there is no telling how they were typed
    op.drop_constraint(op.f("ck_driver_code_lower"), "driver", type_="check")
