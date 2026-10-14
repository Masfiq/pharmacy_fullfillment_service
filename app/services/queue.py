"""Bridge between the database and the pure ``Scheduler``.

The database is the source of truth. For each request we build a Scheduler
from the current rows, let it decide, and persist what it decided. Rebuilding
costs O(n log n) per call, but it means the API holds no in-memory state, so
several API workers can run side by side and a restart loses nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import OrderRow, StationRow
from app.domain.enums import OrderStatus, Role
from app.domain.scheduler import Assignment, QueueEntry, Scheduler
from app.services.audit import apply_transition
from app.services.inventory import InventoryService


def to_entry(order: OrderRow) -> QueueEntry:
    return QueueEntry(
        order_id=order.id,
        priority=order.priority,
        due_by=order.due_by,
        created_at=order.created_at,
        dosage_form=order.drug.dosage_form,
    )


@dataclass
class DispatchResult:
    assigned: list[Assignment] = field(default_factory=list)
    held_order_ids: list[int] = field(default_factory=list)
    remaining_queued: int = 0


class QueueService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ----- reads ----------------------------------------------------------

    def queued_orders(self, lock: bool = False) -> Sequence[OrderRow]:
        stmt = (
            select(OrderRow)
            .options(joinedload(OrderRow.drug, innerjoin=True))
            .where(OrderRow.status == OrderStatus.QUEUED)
        )
        if lock:
            stmt = stmt.with_for_update(skip_locked=True, of=OrderRow)
        return self.session.scalars(stmt).all()

    def queue(self) -> list[OrderRow]:
        """Queued orders in the exact order the scheduler would assign them."""
        orders = {o.id: o for o in self.queued_orders()}
        scheduler = Scheduler()
        for order in orders.values():
            scheduler.enqueue(to_entry(order))
        return [orders[entry.order_id] for entry in scheduler.snapshot()]

    def station_loads(self) -> dict[int, int]:
        loads: dict[int, int] = {}
        for order in self._filling_orders():
            if order.assigned_station_id is not None:
                loads[order.assigned_station_id] = loads.get(order.assigned_station_id, 0) + 1
        return loads

    # ----- scheduling -----------------------------------------------------

    def build_scheduler(self) -> tuple[Scheduler, dict[int, StationRow]]:
        """A Scheduler loaded with every station and its in progress orders.

        Station rows are locked FOR UPDATE (in id order) so two requests cannot
        both see the same free slot and overfill a station.
        """
        stations = self.session.scalars(
            select(StationRow).order_by(StationRow.id).with_for_update()
        ).all()
        in_progress: dict[int, list[QueueEntry]] = {s.id: [] for s in stations}
        for order in self._filling_orders():
            if order.assigned_station_id in in_progress:
                in_progress[order.assigned_station_id].append(to_entry(order))

        scheduler = Scheduler()
        for station in stations:
            scheduler.add_station(
                station.id,
                station.capacity,
                station.dosage_forms,
                station.status,
                in_progress[station.id],
            )
        return scheduler, {s.id: s for s in stations}

    def pick_station(self, order: OrderRow) -> StationRow | None:
        """The station the scheduler would give this one order right now, if any."""
        scheduler, stations = self.build_scheduler()
        scheduler.enqueue(to_entry(order))
        assignments = scheduler.assign()
        return stations[assignments[0].station_id] if assignments else None

    def dispatch(self, actor: Role = Role.SYSTEM) -> DispatchResult:
        """Assign queued orders to stations in priority order and start filling them."""
        scheduler, stations = self.build_scheduler()
        orders = {o.id: o for o in self.queued_orders(lock=True)}
        for order in orders.values():
            scheduler.enqueue(to_entry(order))

        inventory = InventoryService(self.session)
        result = DispatchResult()
        while assignments := scheduler.assign():
            for assignment in assignments:
                order = orders[assignment.order_id]
                if inventory.try_decrement(order.drug_ndc, order.quantity):
                    order.assigned_station_id = assignment.station_id
                    station = stations[assignment.station_id]
                    reason = f"dispatched to {station.name}"
                    apply_transition(self.session, order, OrderStatus.FILLING, reason, actor)
                    result.assigned.append(assignment)
                else:
                    # Out of stock at fill time: hold it and free the slot for the next order.
                    reason = "insufficient stock at fill time"
                    apply_transition(self.session, order, OrderStatus.ON_HOLD, reason, actor)
                    scheduler.complete(assignment.order_id)
                    result.held_order_ids.append(order.id)

        result.remaining_queued = len(scheduler)
        self.session.commit()
        return result

    def _filling_orders(self) -> Sequence[OrderRow]:
        return self.session.scalars(
            select(OrderRow)
            .options(joinedload(OrderRow.drug, innerjoin=True))
            .where(OrderRow.status == OrderStatus.FILLING)
        ).all()
