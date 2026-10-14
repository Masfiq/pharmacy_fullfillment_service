"""Domain entities: plain classes that own their own rules.

These objects know nothing about the database. The service layer persists
equivalent rows with SQLAlchemy, and the simulation uses these classes
directly, so both paths share the same behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain import state_machine
from app.domain.enums import DosageForm, OrderStatus, Priority, Role, StationStatus, UnitType
from app.domain.errors import InsufficientStockError


@dataclass(frozen=True, slots=True)
class Drug:
    ndc: str
    name: str
    strength: str
    dosage_form: DosageForm
    unit_type: UnitType


@dataclass(slots=True)
class InventoryItem:
    drug: Drug
    quantity_on_hand: int
    reorder_threshold: int

    def can_fulfill(self, quantity: int) -> bool:
        return self.quantity_on_hand >= quantity

    def decrement(self, quantity: int) -> None:
        """Remove stock for a fill. Stock can never go negative."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if not self.can_fulfill(quantity):
            raise InsufficientStockError(
                f"Need {quantity} of {self.drug.ndc}, only {self.quantity_on_hand} on hand",
                ndc=self.drug.ndc,
                requested=quantity,
                on_hand=self.quantity_on_hand,
            )
        self.quantity_on_hand -= quantity

    def restock(self, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.quantity_on_hand += quantity

    @property
    def is_low_stock(self) -> bool:
        return self.quantity_on_hand < self.reorder_threshold


@dataclass(slots=True)
class Station:
    id: int
    name: str
    capacity: int
    supported_dosage_forms: frozenset[DosageForm]
    status: StationStatus = StationStatus.ACTIVE

    def supports(self, form: DosageForm) -> bool:
        return form in self.supported_dosage_forms

    @property
    def is_active(self) -> bool:
        return self.status is StationStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One row of the audit trail: who moved which order where, when, and why."""

    order_id: int
    from_status: OrderStatus | None
    to_status: OrderStatus
    at: datetime
    reason: str
    actor: Role = Role.SYSTEM


@dataclass(slots=True)
class Order:
    id: int
    patient_ref: str
    drug: Drug
    quantity: int
    priority: Priority
    due_by: datetime
    created_at: datetime
    status: OrderStatus = OrderStatus.RECEIVED
    assigned_station: int | None = None
    history: list[TransitionRecord] = field(default_factory=list)

    def transition_to(
        self,
        target: OrderStatus,
        at: datetime,
        reason: str = "",
        actor: Role = Role.SYSTEM,
    ) -> TransitionRecord:
        """Move to ``target`` if the state machine allows it, and record the change."""
        state_machine.ensure_transition(self.status, target)
        record = TransitionRecord(self.id, self.status, target, at, reason, actor)
        self.status = target
        if target is OrderStatus.QUEUED:
            self.assigned_station = None  # back in line, no longer at a station
        self.history.append(record)
        return record
