"""fleet care: pre-start checklists and checks, records a fleet manager keeps,
and how often each truck is serviced

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The design's checklist, the same for each type to start with; a fleet
# manager changes it with PUT /v1/checklists/{type}. (label, critical)
DEFAULT_CHECKLIST = [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
                     ("Load restraints", False), ("Damage", False)]
APPEND_ONLY = ("prestart_check", "prestart_item", "vehicle_record")


def upgrade() -> None:
    op.add_column("vehicle", sa.Column("service_every_months", sa.Integer(), nullable=True))
    op.add_column("vehicle", sa.Column("service_every_km", sa.Integer(), nullable=True))

    op.create_table(
        "checklist_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_type", sa.String(length=8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("critical", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklist_item")),
    )
    op.create_index(op.f("ix_checklist_item_vehicle_type"), "checklist_item", ["vehicle_type"])
    items = sa.table("checklist_item", sa.column("vehicle_type", sa.String),
                     sa.column("position", sa.Integer), sa.column("label", sa.String),
                     sa.column("critical", sa.Boolean))
    op.bulk_insert(items, [
        {"vehicle_type": t, "position": i, "label": label, "critical": critical}
        for t in ("van", "rigid", "ute")
        for i, (label, critical) in enumerate(DEFAULT_CHECKLIST, start=1)])

    op.create_table(
        "prestart_check",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("odometer_km", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_prestart_check_run_id_run")),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_prestart_check_vehicle_id_vehicle")),
        sa.ForeignKeyConstraint(["driver_id"], ["driver.id"],
                                name=op.f("fk_prestart_check_driver_id_driver")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prestart_check")),
        sa.UniqueConstraint("message_id", name=op.f("uq_prestart_check_message_id")),
    )
    op.create_index(op.f("ix_prestart_check_run_id"), "prestart_check", ["run_id"])
    op.create_index(op.f("ix_prestart_check_vehicle_id"), "prestart_check", ["vehicle_id"])

    op.create_table(
        "prestart_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("checklist_item_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("critical", sa.Boolean(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("photo_file_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["check_id"], ["prestart_check.id"],
                                name=op.f("fk_prestart_item_check_id_prestart_check")),
        sa.ForeignKeyConstraint(["checklist_item_id"], ["checklist_item.id"],
                                name=op.f("fk_prestart_item_checklist_item_id_checklist_item")),
        sa.ForeignKeyConstraint(["photo_file_id"], ["pod_file.id"],
                                name=op.f("fk_prestart_item_photo_file_id_pod_file")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prestart_item")),
        sa.UniqueConstraint("photo_file_id", name=op.f("uq_prestart_item_photo_file_id")),
    )
    op.create_index(op.f("ix_prestart_item_check_id"), "prestart_item", ["check_id"])

    op.create_table(
        "vehicle_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=True),
        sa.Column("done_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("odometer_km", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_vehicle_record_vehicle_id_vehicle")),
        sa.ForeignKeyConstraint(["check_id"], ["prestart_check.id"],
                                name=op.f("fk_vehicle_record_check_id_prestart_check")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_record")),
    )
    op.create_index(op.f("ix_vehicle_record_vehicle_id"), "vehicle_record", ["vehicle_id"])

    # kept as they were sent, like a consignment's history (block_change is 0001's)
    for table in APPEND_ONLY:
        op.execute(f"create trigger {table}_append_only before update or delete on {table} "
                   "for each row execute function block_change()")


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f"drop trigger if exists {table}_append_only on {table}")
    op.drop_index(op.f("ix_vehicle_record_vehicle_id"), table_name="vehicle_record")
    op.drop_table("vehicle_record")
    op.drop_index(op.f("ix_prestart_item_check_id"), table_name="prestart_item")
    op.drop_table("prestart_item")
    op.drop_index(op.f("ix_prestart_check_vehicle_id"), table_name="prestart_check")
    op.drop_index(op.f("ix_prestart_check_run_id"), table_name="prestart_check")
    op.drop_table("prestart_check")
    op.drop_index(op.f("ix_checklist_item_vehicle_type"), table_name="checklist_item")
    op.drop_table("checklist_item")
    op.drop_column("vehicle", "service_every_km")
    op.drop_column("vehicle", "service_every_months")
