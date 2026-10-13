"""live location: where each run's phone was, kept for the retention period

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # no append-only trigger: positions are personal information, deleted
    # after the retention period, not business history
    op.create_table(
        "position",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("accuracy_m", sa.Numeric(8, 1), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_position_run_id_run")),
        sa.ForeignKeyConstraint(["driver_id"], ["driver.id"],
                                name=op.f("fk_position_driver_id_driver")),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_position_vehicle_id_vehicle")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position")),
    )
    op.create_index("ix_position_run_id_received_at", "position", ["run_id", "received_at"])
    op.create_index(op.f("ix_position_recorded_at"), "position", ["recorded_at"])
    # the proofs of delivery whose position the worker will clear one day
    op.create_index("ix_pod_at_located", "pod", ["at"],
                    postgresql_where=sa.text("latitude is not null"))


def downgrade() -> None:
    op.drop_index("ix_pod_at_located", table_name="pod")
    op.drop_index(op.f("ix_position_recorded_at"), table_name="position")
    op.drop_index("ix_position_run_id_received_at", table_name="position")
    op.drop_table("position")
