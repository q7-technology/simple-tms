"""runs and stops, the ship-call and print-job queues, API keys

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        _at(),
        sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_run_vehicle_id_vehicle")),
        sa.ForeignKeyConstraint(["driver_id"], ["driver.id"], name=op.f("fk_run_driver_id_driver")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run")),
    )
    op.create_index(op.f("ix_run_day"), "run", ["day"])
    op.create_index(op.f("ix_run_vehicle_id"), "run", ["vehicle_id"])
    op.create_index(op.f("ix_run_driver_id"), "run", ["driver_id"])
    op.create_index("uq_run_vehicle_id_day", "run", ["vehicle_id", "day"], unique=True,
                    postgresql_where=sa.text("status <> 'cancelled'"))

    op.create_table(
        "stop",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("added_by", sa.String(length=64), nullable=False),
        _at("added_at"),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_stop_run_id_run")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_stop_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stop")),
    )
    op.create_index(op.f("ix_stop_run_id"), "stop", ["run_id"])
    op.create_index("uq_stop_consignment_id_live", "stop", ["consignment_id"], unique=True,
                    postgresql_where=sa.text("removed_at is null"))

    op.create_table(
        "outbound_call",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        _at(),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_outbound_call_run_id_run")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_outbound_call_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbound_call")),
        sa.UniqueConstraint("message_id", name=op.f("uq_outbound_call_message_id")),
    )
    op.create_index(op.f("ix_outbound_call_run_id"), "outbound_call", ["run_id"])
    op.create_index(op.f("ix_outbound_call_consignment_id"), "outbound_call", ["consignment_id"])
    op.create_index("ix_outbound_call_due", "outbound_call", ["status", "next_attempt_at"])

    op.create_table(
        "print_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("printer", sa.String(length=64), nullable=False),
        sa.Column("copies", sa.Integer(), nullable=False),
        sa.Column("reference", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        _at(),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_print_job_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_print_job")),
        sa.UniqueConstraint("job_id", name=op.f("uq_print_job_job_id")),
    )
    op.create_index(op.f("ix_print_job_run_id"), "print_job", ["run_id"])
    op.create_index("ix_print_job_due", "print_job", ["status", "next_attempt_at"])

    op.create_table(
        "api_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_key")),
        sa.UniqueConstraint("name", name=op.f("uq_api_key_name")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_key_key_hash")),
    )


def downgrade() -> None:
    op.drop_table("api_key")
    op.drop_index("ix_print_job_due", table_name="print_job")
    op.drop_index(op.f("ix_print_job_run_id"), table_name="print_job")
    op.drop_table("print_job")
    op.drop_index("ix_outbound_call_due", table_name="outbound_call")
    op.drop_index(op.f("ix_outbound_call_consignment_id"), table_name="outbound_call")
    op.drop_index(op.f("ix_outbound_call_run_id"), table_name="outbound_call")
    op.drop_table("outbound_call")
    op.drop_index("uq_stop_consignment_id_live", table_name="stop")
    op.drop_index(op.f("ix_stop_run_id"), table_name="stop")
    op.drop_table("stop")
    op.drop_index("uq_run_vehicle_id_day", table_name="run")
    op.drop_index(op.f("ix_run_driver_id"), table_name="run")
    op.drop_index(op.f("ix_run_vehicle_id"), table_name="run")
    op.drop_index(op.f("ix_run_day"), table_name="run")
    op.drop_table("run")
