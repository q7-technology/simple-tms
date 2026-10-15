"""settings an admin gives on screen in place of .env's, and the record of
each change (append-only)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "setting",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_setting")),
    )
    op.create_table(
        "setting_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("secret", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_setting_change")),
    )
    op.create_index(op.f("ix_setting_change_name"), "setting_change", ["name"])
    # each change is kept as it was made (block_change is 0001's)
    op.execute("create trigger setting_change_append_only before update or delete on "
               "setting_change for each row execute function block_change()")


def downgrade() -> None:
    op.execute("drop trigger if exists setting_change_append_only on setting_change")
    op.drop_index(op.f("ix_setting_change_name"), table_name="setting_change")
    op.drop_table("setting_change")
    op.drop_table("setting")
