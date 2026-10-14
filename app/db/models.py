"""SQLAlchemy ORM models (database rows).

Business rules live in ``app.domain``. These models describe storage, and the
CHECK constraints here are a second line of defense: even a buggy code path
cannot write negative stock or an unknown status.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import DosageForm, OrderStatus, Priority, Role, StationStatus, UnitType

# Predictable constraint names make Alembic migrations stable and readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    # native_enum=False stores a VARCHAR plus a CHECK constraint. Adding a new
    # value later is then a simple migration instead of ALTER TYPE.
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DrugRow(Base):
    __tablename__ = "drugs"

    ndc: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    strength: Mapped[str] = mapped_column(String(120))
    dosage_form: Mapped[DosageForm] = mapped_column(_enum(DosageForm, "dosage_form"))
    unit_type: Mapped[UnitType] = mapped_column(_enum(UnitType, "unit_type"))

    inventory: Mapped[InventoryItemRow | None] = relationship(back_populates="drug")


class InventoryItemRow(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="non_negative_stock"),
        CheckConstraint("reorder_threshold >= 0", name="non_negative_threshold"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drug_ndc: Mapped[str] = mapped_column(ForeignKey("drugs.ndc"), unique=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer)
    reorder_threshold: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    drug: Mapped[DrugRow] = relationship(back_populates="inventory")


class StationRow(Base):
    __tablename__ = "stations"
    __table_args__ = (CheckConstraint("capacity > 0", name="positive_capacity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    capacity: Mapped[int] = mapped_column(Integer)
    supported_dosage_forms: Mapped[list[str]] = mapped_column(ARRAY(String(20)))
    status: Mapped[StationStatus] = mapped_column(
        _enum(StationStatus, "station_status"), default=StationStatus.ACTIVE
    )

    @property
    def dosage_forms(self) -> frozenset[DosageForm]:
        return frozenset(DosageForm(form) for form in self.supported_dosage_forms)


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        Index("ix_orders_status_priority", "status", "priority"),
        Index("ix_orders_drug_status", "drug_ndc", "status"),
        Index("ix_orders_station_status", "assigned_station_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_ref: Mapped[str] = mapped_column(String(40))
    drug_ndc: Mapped[str] = mapped_column(ForeignKey("drugs.ndc"))
    quantity: Mapped[int] = mapped_column(Integer)
    priority: Mapped[Priority] = mapped_column(_enum(Priority, "priority"))
    due_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "order_status"), default=OrderStatus.RECEIVED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    assigned_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("stations.id"), nullable=True
    )

    drug: Mapped[DrugRow] = relationship()
    assigned_station: Mapped[StationRow | None] = relationship()
    audit_log: Mapped[list[AuditLogRow]] = relationship(
        back_populates="order", order_by="AuditLogRow.id"
    )


class AuditLogRow(Base):
    """Append only trail of every status change. Rows are never updated or deleted."""

    __tablename__ = "order_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    from_status: Mapped[OrderStatus | None] = mapped_column(
        _enum(OrderStatus, "audit_from_status"), nullable=True
    )
    to_status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus, "audit_to_status"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[Role] = mapped_column(_enum(Role, "actor_role"), default=Role.SYSTEM)

    order: Mapped[OrderRow] = relationship(back_populates="audit_log")
