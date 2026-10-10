"""skeleton: users, drivers, vehicles, consignments, seen WMS events

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _created_at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
    )
    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("refresh_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=op.f("fk_user_session_user_id_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_session")),
        sa.UniqueConstraint("refresh_hash", name=op.f("uq_user_session_refresh_hash")),
    )
    op.create_index(op.f("ix_user_session_user_id"), "user_session", ["user_id"])
    op.create_table(
        "driver",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("pin_hash", sa.String(length=200), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_driver")),
        sa.UniqueConstraint("code", name=op.f("uq_driver_code")),
    )
    op.create_table(
        "vehicle",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rego", sa.String(length=12), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("max_kg", sa.Numeric(18, 6), nullable=False),
        sa.Column("max_m3", sa.Numeric(18, 6), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle")),
        sa.UniqueConstraint("rego", name=op.f("uq_vehicle_rego")),
    )
    op.create_table(
        "consignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("wms_ref", sa.String(length=64), nullable=False),
        sa.Column("warehouse", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=32), nullable=False),
        sa.Column("customer", sa.String(length=200), nullable=False),
        sa.Column("ship_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_by", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consignment")),
        sa.UniqueConstraint("owner", "wms_ref", name=op.f("uq_consignment_owner_wms_ref")),
    )
    op.create_index(op.f("ix_consignment_status"), "consignment", ["status"])
    op.create_table(
        "package",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("package_no", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("weight_kg", sa.Numeric(18, 6), nullable=True),
        sa.Column("length_cm", sa.Numeric(18, 6), nullable=True),
        sa.Column("width_cm", sa.Numeric(18, 6), nullable=True),
        sa.Column("height_cm", sa.Numeric(18, 6), nullable=True),
        sa.Column("sscc", sa.String(length=18), nullable=True),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_package_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package")),
        sa.UniqueConstraint("consignment_id", "package_no",
                            name=op.f("uq_package_consignment_id_package_no")),
    )
    op.create_index(op.f("ix_package_consignment_id"), "package", ["consignment_id"])
    op.create_table(
        "consignment_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        _created_at("at"),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_consignment_event_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consignment_event")),
    )
    op.create_index(op.f("ix_consignment_event_consignment_id"), "consignment_event",
                    ["consignment_id"])
    op.create_table(
        "inbound_event",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        _created_at("received_at"),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_inbound_event")),
    )

    # A consignment's history is append-only. A mistake is corrected by a new row.
    op.execute("""
        create or replace function block_change() returns trigger
        language plpgsql as $$
        begin
            raise exception '% is append-only: rows cannot be updated or deleted', tg_table_name
                using errcode = 'restrict_violation';
        end
        $$
    """)
    op.execute(
        "create trigger consignment_event_append_only before update or delete "
        "on consignment_event for each row execute function block_change()"
    )


def downgrade() -> None:
    op.execute("drop trigger if exists consignment_event_append_only on consignment_event")
    op.execute("drop function if exists block_change()")
    op.drop_table("inbound_event")
    op.drop_index(op.f("ix_consignment_event_consignment_id"), table_name="consignment_event")
    op.drop_table("consignment_event")
    op.drop_index(op.f("ix_package_consignment_id"), table_name="package")
    op.drop_table("package")
    op.drop_index(op.f("ix_consignment_status"), table_name="consignment")
    op.drop_table("consignment")
    op.drop_table("vehicle")
    op.drop_table("driver")
    op.drop_index(op.f("ix_user_session_user_id"), table_name="user_session")
    op.drop_table("user_session")
    op.drop_table("user")
