"""carriers: who else takes freight, each carrier's manifest for a pickup,
and each consignment's booking on one with its con note; a ship call and a
print job can be a manifest's

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUEUES = ("outbound_call", "print_job")


def _at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "carrier",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("account", sa.String(length=40), nullable=True),
        sa.Column("pickup_time", sa.Time(), nullable=True),
        sa.Column("adapter", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carrier")),
        sa.UniqueConstraint("name", name=op.f("uq_carrier_name")),
    )
    op.create_table(
        "manifest",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("carrier_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        _at(),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["carrier_id"], ["carrier.id"],
                                name=op.f("fk_manifest_carrier_id_carrier")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manifest")),
    )
    op.create_index(op.f("ix_manifest_carrier_id"), "manifest", ["carrier_id"])
    op.create_index(op.f("ix_manifest_day"), "manifest", ["day"])
    op.create_index("uq_manifest_carrier_id_day_open", "manifest", ["carrier_id", "day"],
                    unique=True, postgresql_where=sa.text("collected_at is null"))
    op.create_table(
        "booking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("manifest_id", sa.Integer(), nullable=False),
        sa.Column("carrier_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("con_note", sa.String(length=40), nullable=True),
        sa.Column("added_by", sa.String(length=64), nullable=False),
        _at("added_at"),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["manifest_id"], ["manifest.id"],
                                name=op.f("fk_booking_manifest_id_manifest")),
        sa.ForeignKeyConstraint(["carrier_id"], ["carrier.id"],
                                name=op.f("fk_booking_carrier_id_carrier")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_booking_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking")),
    )
    op.create_index(op.f("ix_booking_manifest_id"), "booking", ["manifest_id"])
    op.create_index("uq_booking_consignment_id_live", "booking", ["consignment_id"],
                    unique=True, postgresql_where=sa.text("removed_at is null"))
    op.create_index("uq_booking_carrier_id_con_note", "booking", ["carrier_id", "con_note"],
                    unique=True,
                    postgresql_where=sa.text("removed_at is null and con_note is not null"))

    # a ship call or a print job is a run's or, now, a manifest's
    for table in QUEUES:
        op.add_column(table, sa.Column("manifest_id", sa.Integer(), nullable=True))
        op.create_foreign_key(op.f(f"fk_{table}_manifest_id_manifest"), table, "manifest",
                              ["manifest_id"], ["id"])
        op.create_index(op.f(f"ix_{table}_manifest_id"), table, ["manifest_id"])
        op.alter_column(table, "run_id", existing_type=sa.Integer(), nullable=True)
        op.create_check_constraint(op.f(f"ck_{table}_run_or_manifest"), table,
                                   "num_nonnulls(run_id, manifest_id) = 1")


def downgrade() -> None:
    """Refused while a manifest's ship call is still to be made (pending, or
    failed and waiting for Try again): it is a delivery Simple WMS hasn't
    heard has shipped, and going back would lose it. Let the worker make them
    (or settle them by hand) first. Ship calls already made, and a
    manifest's print jobs, go with the manifests."""
    waiting = op.get_bind().execute(sa.text(
        "select count(*) from outbound_call "
        "where manifest_id is not null and status in ('pending', 'failed')")).scalar()
    if waiting:
        raise RuntimeError(
            f"{waiting} ship call{'' if waiting == 1 else 's'} of a carrier's manifest still to "
            "be made (pending or failed): Simple WMS hasn't heard those deliveries shipped. "
            "Let the worker make them, or Try again on Carriers, then downgrade again.")
    for table in QUEUES:
        # a manifest's calls and prints can't be a run's: they go with the manifests
        op.execute(f"delete from {table} where manifest_id is not null")
        op.drop_constraint(op.f(f"ck_{table}_run_or_manifest"), table, type_="check")
        op.alter_column(table, "run_id", existing_type=sa.Integer(), nullable=False)
        op.drop_index(op.f(f"ix_{table}_manifest_id"), table_name=table)
        op.drop_constraint(op.f(f"fk_{table}_manifest_id_manifest"), table, type_="foreignkey")
        op.drop_column(table, "manifest_id")
    op.drop_index("uq_booking_carrier_id_con_note", table_name="booking")
    op.drop_index("uq_booking_consignment_id_live", table_name="booking")
    op.drop_index(op.f("ix_booking_manifest_id"), table_name="booking")
    op.drop_table("booking")
    op.drop_index("uq_manifest_carrier_id_day_open", table_name="manifest")
    op.drop_index(op.f("ix_manifest_day"), table_name="manifest")
    op.drop_index(op.f("ix_manifest_carrier_id"), table_name="manifest")
    op.drop_table("manifest")
    op.drop_table("carrier")
