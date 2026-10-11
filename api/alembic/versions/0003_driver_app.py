"""the driver app: stop outcomes, proof of delivery and its files, seen messages

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.add_column("run", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stop", sa.Column("outcome", sa.String(length=16), nullable=True))
    op.add_column("stop", sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True))
    # a stop with an outcome is a record of the trip, so a consignment that
    # came back can go on another run
    op.drop_index("uq_stop_consignment_id_live", table_name="stop")
    op.create_index("uq_stop_consignment_id_live", "stop", ["consignment_id"], unique=True,
                    postgresql_where=sa.text("removed_at is null and outcome is null"))

    op.create_table(
        "inbound_message",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("sender", sa.String(length=40), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _at("received_at"),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("last_duplicate_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_inbound_message")),
    )

    op.create_table(
        "pod_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("uploaded_by", sa.String(length=40), nullable=False),
        _at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pod_file")),
    )

    op.create_table(
        "pod",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("stop_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("signed_name", sa.String(length=120), nullable=True),
        sa.Column("signature_file_id", sa.Uuid(), nullable=True),
        sa.Column("photo_file_id", sa.Uuid(), nullable=True),
        sa.Column("packages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("accuracy_m", sa.Numeric(8, 1), nullable=True),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        _at(),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_pod_run_id_run")),
        sa.ForeignKeyConstraint(["stop_id"], ["stop.id"], name=op.f("fk_pod_stop_id_stop")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_pod_consignment_id_consignment")),
        sa.ForeignKeyConstraint(["signature_file_id"], ["pod_file.id"],
                                name=op.f("fk_pod_signature_file_id_pod_file")),
        sa.ForeignKeyConstraint(["photo_file_id"], ["pod_file.id"],
                                name=op.f("fk_pod_photo_file_id_pod_file")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pod")),
        sa.UniqueConstraint("message_id", name=op.f("uq_pod_message_id")),
        sa.UniqueConstraint("stop_id", name=op.f("uq_pod_stop_id")),
        sa.UniqueConstraint("signature_file_id", name=op.f("uq_pod_signature_file_id")),
        sa.UniqueConstraint("photo_file_id", name=op.f("uq_pod_photo_file_id")),
    )
    op.create_index(op.f("ix_pod_run_id"), "pod", ["run_id"])
    op.create_index(op.f("ix_pod_consignment_id"), "pod", ["consignment_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_pod_consignment_id"), table_name="pod")
    op.drop_index(op.f("ix_pod_run_id"), table_name="pod")
    op.drop_table("pod")
    op.drop_table("pod_file")
    op.drop_table("inbound_message")
    op.drop_index("uq_stop_consignment_id_live", table_name="stop")
    op.create_index("uq_stop_consignment_id_live", "stop", ["consignment_id"], unique=True,
                    postgresql_where=sa.text("removed_at is null"))
    op.drop_column("stop", "outcome_at")
    op.drop_column("stop", "outcome")
    op.drop_column("run", "finished_at")
