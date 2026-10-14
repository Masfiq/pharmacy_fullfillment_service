"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-13

Started from ``alembic revision --autogenerate`` and then cleaned up by hand:
enum columns are written as VARCHAR plus one explicitly named CHECK
constraint, which is exactly what the models produce.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOSAGE_FORMS = ("TABLET", "CAPSULE", "LIQUID", "INJECTABLE", "TOPICAL", "INHALER", "OTHER")
UNIT_TYPES = ("EACH", "ML", "G")
STATION_STATUSES = ("ACTIVE", "DOWN")
PRIORITIES = ("STAT", "URGENT", "ROUTINE")
ORDER_STATUSES = (
    "RECEIVED", "VALIDATED", "QUEUED", "FILLING", "VERIFIED", "SHIPPED", "ON_HOLD", "CANCELLED",
)  # fmt: skip
ROLES = ("TECHNICIAN", "PHARMACIST", "SYSTEM")


def one_of(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(name))


def upgrade() -> None:
    op.create_table(
        "drugs",
        sa.Column("ndc", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("strength", sa.String(120), nullable=False),
        sa.Column("dosage_form", sa.String(20), nullable=False),
        sa.Column("unit_type", sa.String(20), nullable=False),
        one_of("dosage_form", DOSAGE_FORMS, "ck_drugs_dosage_form"),
        one_of("unit_type", UNIT_TYPES, "ck_drugs_unit_type"),
        sa.PrimaryKeyConstraint("ndc", name=op.f("pk_drugs")),
    )
    op.create_table(
        "stations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("supported_dosage_forms", postgresql.ARRAY(sa.String(20)), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        one_of("status", STATION_STATUSES, "ck_stations_station_status"),
        sa.CheckConstraint("capacity > 0", name=op.f("ck_stations_positive_capacity")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stations")),
        sa.UniqueConstraint("name", name=op.f("uq_stations_name")),
    )
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drug_ndc", sa.String(20), nullable=False),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False),
        sa.Column("reorder_threshold", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity_on_hand >= 0", name=op.f("ck_inventory_items_non_negative_stock")
        ),
        sa.CheckConstraint(
            "reorder_threshold >= 0", name=op.f("ck_inventory_items_non_negative_threshold")
        ),
        sa.ForeignKeyConstraint(
            ["drug_ndc"], ["drugs.ndc"], name=op.f("fk_inventory_items_drug_ndc_drugs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_items")),
        sa.UniqueConstraint("drug_ndc", name=op.f("uq_inventory_items_drug_ndc")),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_ref", sa.String(40), nullable=False),
        sa.Column("drug_ndc", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("due_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_station_id", sa.Integer(), nullable=True),
        one_of("priority", PRIORITIES, "ck_orders_priority"),
        one_of("status", ORDER_STATUSES, "ck_orders_order_status"),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_orders_positive_quantity")),
        sa.ForeignKeyConstraint(
            ["assigned_station_id"],
            ["stations.id"],
            name=op.f("fk_orders_assigned_station_id_stations"),
        ),
        sa.ForeignKeyConstraint(["drug_ndc"], ["drugs.ndc"], name=op.f("fk_orders_drug_ndc_drugs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index("ix_orders_drug_status", "orders", ["drug_ndc", "status"])
    op.create_index("ix_orders_station_status", "orders", ["assigned_station_id", "status"])
    op.create_index("ix_orders_status_priority", "orders", ["status", "priority"])
    op.create_table(
        "order_audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(20), nullable=False),
        one_of("from_status", ORDER_STATUSES, "ck_order_audit_log_audit_from_status"),
        one_of("to_status", ORDER_STATUSES, "ck_order_audit_log_audit_to_status"),
        one_of("actor", ROLES, "ck_order_audit_log_actor_role"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_audit_log_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_audit_log")),
    )
    op.create_index(op.f("ix_order_audit_log_order_id"), "order_audit_log", ["order_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_order_audit_log_order_id"), table_name="order_audit_log")
    op.drop_table("order_audit_log")
    op.drop_index("ix_orders_status_priority", table_name="orders")
    op.drop_index("ix_orders_station_status", table_name="orders")
    op.drop_index("ix_orders_drug_status", table_name="orders")
    op.drop_table("orders")
    op.drop_table("inventory_items")
    op.drop_table("stations")
    op.drop_table("drugs")
