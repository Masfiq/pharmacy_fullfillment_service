"""Order use cases: create, read, list, and move through the workflow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.models import AuditLogRow, DrugRow, OrderRow, utcnow
from app.domain import state_machine
from app.domain.enums import OrderStatus, Priority, Role
from app.domain.errors import (
    InsufficientStockError,
    NoStationAvailableError,
    NotFoundError,
    PermissionDeniedError,
)
from app.services.audit import apply_transition
from app.services.inventory import InventoryService
from app.services.queue import QueueService

S = OrderStatus

# Audit reason used when the caller does not give one.
DEFAULT_REASONS: dict[OrderStatus, str] = {
    S.QUEUED: "released to fill queue",
    S.VERIFIED: "pharmacist verified fill",
    S.SHIPPED: "shipped",
    S.ON_HOLD: "placed on hold",
}


@dataclass(frozen=True, slots=True)
class NewOrder:
    patient_ref: str
    drug_ndc: str
    quantity: int
    priority: Priority = Priority.ROUTINE
    due_by: datetime | None = None


class OrderService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.inventory = InventoryService(session)

    # ----- create and read ------------------------------------------------

    def create(self, new: NewOrder, actor: Role = Role.TECHNICIAN) -> OrderRow:
        if self.session.get(DrugRow, new.drug_ndc) is None:
            raise NotFoundError(f"Unknown drug NDC {new.drug_ndc}", ndc=new.drug_ndc)
        created_at = utcnow()
        due_by = new.due_by or created_at + new.priority.default_turnaround
        if due_by.tzinfo is None:
            due_by = due_by.replace(tzinfo=UTC)
        order = OrderRow(
            patient_ref=new.patient_ref,
            drug_ndc=new.drug_ndc,
            quantity=new.quantity,
            priority=new.priority,
            due_by=due_by,
            status=S.RECEIVED,
            created_at=created_at,
        )
        self.session.add(order)
        self.session.flush()  # assigns order.id for the audit row
        self.session.add(
            AuditLogRow(
                order_id=order.id,
                from_status=None,
                to_status=S.RECEIVED,
                timestamp=created_at,
                reason="order received",
                actor=actor,
            )
        )
        self.session.commit()
        return self.get(order.id)

    def get(self, order_id: int) -> OrderRow:
        stmt = (
            select(OrderRow)
            .options(joinedload(OrderRow.drug), selectinload(OrderRow.audit_log))
            .where(OrderRow.id == order_id)
            .execution_options(populate_existing=True)
        )
        order = self.session.scalars(stmt).one_or_none()
        if order is None:
            raise NotFoundError(f"Order {order_id} not found", order_id=order_id)
        return order

    def list_orders(
        self,
        status: OrderStatus | None = None,
        priority: Priority | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[OrderRow], int]:
        filters = []
        if status is not None:
            filters.append(OrderRow.status == status)
        if priority is not None:
            filters.append(OrderRow.priority == priority)
        total = self.session.scalar(select(func.count()).select_from(OrderRow).where(*filters))
        rows = self.session.scalars(
            select(OrderRow)
            .options(joinedload(OrderRow.drug))
            .where(*filters)
            .order_by(OrderRow.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return rows, total or 0

    # ----- workflow -------------------------------------------------------

    def transition(
        self,
        order_id: int,
        target: OrderStatus | None = None,
        reason: str | None = None,
        actor: Role = Role.TECHNICIAN,
    ) -> OrderRow:
        """Move an order to ``target`` (or its default next status).

        Some steps have side effects decided by the system, so the resulting
        status can differ from the one requested: validating or filling an
        order that is short on stock puts it ON_HOLD instead. The audit log
        records why.
        """
        order = self._lock(order_id)
        target = target or state_machine.next_status(order.status)
        state_machine.ensure_transition(order.status, target)  # fail before any side effect
        if target is S.VERIFIED and actor is not Role.PHARMACIST:
            raise PermissionDeniedError(
                "Only a pharmacist can verify a filled order", required_role="PHARMACIST"
            )

        if target is S.VALIDATED:
            self._validate(order, reason, actor)
        elif target is S.FILLING:
            self._start_filling(order, reason, actor)
        elif target is S.CANCELLED:
            self._cancel(order, reason, actor)
        elif order.status is S.FILLING and target is S.QUEUED:
            self.inventory.give_back(order.drug_ndc, order.quantity)
            apply_transition(self.session, order, S.QUEUED, reason or "returned to queue", actor)
        else:
            apply_transition(self.session, order, target, reason or DEFAULT_REASONS[target], actor)

        self.session.commit()
        return self.get(order_id)

    def cancel(self, order_id: int, reason: str | None, actor: Role) -> OrderRow:
        return self.transition(order_id, S.CANCELLED, reason, actor)

    # ----- steps with side effects ----------------------------------------

    def _validate(self, order: OrderRow, reason: str | None, actor: Role) -> None:
        on_hand = self.inventory.on_hand(order.drug_ndc)
        if on_hand >= order.quantity:
            apply_transition(
                self.session, order, S.VALIDATED, reason or "stock check passed", actor
            )
            return
        if order.status is S.ON_HOLD:
            # Still short: leave it on hold rather than writing a no-op audit row.
            raise InsufficientStockError(
                f"Still not enough stock for {order.drug_ndc}: need {order.quantity}, "
                f"have {on_hand}",
                ndc=order.drug_ndc,
                requested=order.quantity,
                on_hand=on_hand,
            )
        hold_reason = f"insufficient stock: need {order.quantity}, have {on_hand}"
        apply_transition(self.session, order, S.ON_HOLD, hold_reason, Role.SYSTEM)

    def _start_filling(self, order: OrderRow, reason: str | None, actor: Role) -> None:
        """Manual fill of one specific order (a technician override).

        The normal path is ``QueueService.dispatch``, which fills the queue in
        priority order. Both use the same scheduler rules to pick a station.
        """
        station = QueueService(self.session).pick_station(order)
        if station is None:
            raise NoStationAvailableError(
                f"No ACTIVE station with free capacity supports {order.drug.dosage_form.value}",
                dosage_form=order.drug.dosage_form.value,
            )
        if not self.inventory.try_decrement(order.drug_ndc, order.quantity):
            apply_transition(
                self.session, order, S.ON_HOLD, "insufficient stock at fill time", Role.SYSTEM
            )
            return
        order.assigned_station_id = station.id
        apply_transition(
            self.session, order, S.FILLING, reason or f"assigned to {station.name}", actor
        )

    def _cancel(self, order: OrderRow, reason: str | None, actor: Role) -> None:
        if order.status is S.FILLING:
            self.inventory.give_back(order.drug_ndc, order.quantity)  # stock was already taken
        apply_transition(self.session, order, S.CANCELLED, reason or "cancelled", actor)

    def _lock(self, order_id: int) -> OrderRow:
        """Load the order with a row lock so two requests cannot move it at once."""
        stmt = (
            select(OrderRow)
            .options(joinedload(OrderRow.drug, innerjoin=True))
            .where(OrderRow.id == order_id)
            .with_for_update(of=OrderRow)
        )
        order = self.session.scalars(stmt).one_or_none()
        if order is None:
            raise NotFoundError(f"Order {order_id} not found", order_id=order_id)
        return order
