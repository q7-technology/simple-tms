"""a proof of delivery is kept as it was: the only change allowed is
clearing where it was, after the retention period

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WHERE = "array['latitude', 'longitude', 'accuracy_m']"


def upgrade() -> None:
    # A delivery is recorded once and kept (rules 1 and 2). Where it was is
    # personal information: the worker clears it after the retention period,
    # and that is the one change a pod row may have.
    op.execute(f"""
        create or replace function pod_only_forget() returns trigger
        language plpgsql as $$
        begin
            if tg_op = 'UPDATE'
               and new.latitude is null and new.longitude is null and new.accuracy_m is null
               and to_jsonb(new) - {WHERE} = to_jsonb(old) - {WHERE} then
                return new;
            end if;
            raise exception 'pod is kept as it was: only where a delivery was may be '
                            'cleared, after the retention period'
                using errcode = 'restrict_violation';
        end
        $$
    """)
    op.execute("create trigger pod_kept before update or delete on pod "
               "for each row execute function pod_only_forget()")


def downgrade() -> None:
    op.execute("drop trigger if exists pod_kept on pod")
    op.execute("drop function if exists pod_only_forget()")
