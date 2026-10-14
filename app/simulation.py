"""Discrete time simulation of the fulfillment floor.

The simulation runs entirely in memory on the domain classes (``Order``,
``InventoryItem``, ``Scheduler``), so it obeys exactly the same state machine
and scheduling rules as the API, but it never writes to the operational
database. It takes a snapshot of drugs, stock, and stations as its starting
point. With a fixed seed, a run is fully reproducible.

One tick is one simulated minute. Each tick, in this order:

1. Stations may fail (``station_failure_rate``) or come back after repair.
   A failed station's orders go back to the queue and their stock is returned.
2. Fills that are done move FILLING -> VERIFIED, and orders verified on an
   earlier tick move VERIFIED -> SHIPPED.
3. New orders arrive and are validated against stock (VALIDATED -> QUEUED,
   or ON_HOLD if short).
4. The scheduler assigns queued orders to free compatible stations; stock is
   decremented when filling starts (ON_HOLD if it ran out in the meantime).
5. Station utilization is sampled.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.entities import Drug, InventoryItem, Order, Station
from app.domain.enums import DosageForm, OrderStatus, Priority, Role, StationStatus
from app.domain.errors import InsufficientStockError
from app.domain.scheduler import QueueEntry, Scheduler
from app.domain.synthetic import SyntheticDataGenerator

S = OrderStatus

# Minutes of station time to fill one order, by dosage form.
FILL_TICKS: dict[DosageForm, int] = {
    DosageForm.TABLET: 3,
    DosageForm.CAPSULE: 3,
    DosageForm.LIQUID: 6,
    DosageForm.INJECTABLE: 8,
    DosageForm.TOPICAL: 4,
    DosageForm.INHALER: 2,
    DosageForm.OTHER: 5,
}

SIM_START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    orders: int = 2000
    ticks: int = 480
    seed: int | None = 42
    station_failure_rate: float = 0.002  # chance per ACTIVE station per tick
    repair_ticks: int = 30


@dataclass(frozen=True, slots=True)
class StationUsage:
    station_id: int
    name: str
    capacity: int
    busy_slot_ticks: int
    down_ticks: int
    utilization: float  # busy slot ticks / (capacity * ticks)


@dataclass(frozen=True, slots=True)
class SimulationReport:
    config: SimulationConfig
    orders_generated: int
    orders_shipped: int
    orders_on_hold: int
    status_counts: dict[str, int]
    average_wait_minutes: dict[str, float | None]
    average_turnaround_minutes: dict[str, float | None]
    on_time_rate: dict[str, float | None]
    station_utilization: list[StationUsage]
    overall_utilization: float
    requeued_after_station_down: int
    station_failures: int
    audit_records: int
    elapsed_ms: float


@dataclass(slots=True)
class _Tracked:
    order: Order
    arrival_tick: int
    fill_started_tick: int | None = None
    fill_done_tick: int | None = None
    verified_tick: int | None = None
    shipped_tick: int | None = None


@dataclass(slots=True)
class _StationState:
    station: Station
    busy_slot_ticks: int = 0
    down_ticks: int = 0
    repair_at: int | None = None


@dataclass
class Simulation:
    drugs: Sequence[Drug]
    inventory: dict[str, InventoryItem]
    stations: Sequence[Station]
    config: SimulationConfig = field(default_factory=SimulationConfig)

    def run(self) -> SimulationReport:
        started = time.perf_counter()
        gen = SyntheticDataGenerator(self.config.seed)
        rng = gen.rng
        inventory = copy.deepcopy(self.inventory)  # never mutate the caller's snapshot
        scheduler = Scheduler()
        floor: dict[int, _StationState] = {}
        for station in self.stations:
            st = _StationState(copy.copy(station))
            floor[station.id] = st
            scheduler.add_station(
                station.id, station.capacity, station.supported_dosage_forms, station.status
            )

        arrivals: dict[int, list[_Tracked]] = {}
        tracked: dict[int, _Tracked] = {}
        stocked_drugs = [d for d in self.drugs if d.ndc in inventory]
        for order_id in range(1, self.config.orders + 1):
            spec = gen.order(stocked_drugs)
            tick = rng.randrange(self.config.ticks)
            created = SIM_START + timedelta(minutes=tick)
            order = Order(
                id=order_id,
                patient_ref=spec.patient_ref,
                drug=spec.drug,
                quantity=spec.quantity,
                priority=spec.priority,
                due_by=created + spec.priority.default_turnaround,
                created_at=created,
            )
            item = _Tracked(order, tick)
            tracked[order_id] = item
            arrivals.setdefault(tick, []).append(item)

        requeued = failures = 0
        for tick in range(self.config.ticks):
            now = SIM_START + timedelta(minutes=tick)

            # 1. station failures and repairs
            for st in floor.values():
                if st.station.status is StationStatus.DOWN:
                    if st.repair_at == tick:
                        st.station.status = StationStatus.ACTIVE
                        scheduler.mark_station_active(st.station.id)
                    else:
                        st.down_ticks += 1
                        continue
                if rng.random() < self.config.station_failure_rate:
                    failures += 1
                    st.station.status = StationStatus.DOWN
                    st.repair_at = tick + self.config.repair_ticks
                    st.down_ticks += 1
                    for order_id in scheduler.mark_station_down(st.station.id):
                        t = tracked[order_id]
                        inventory[t.order.drug.ndc].restock(t.order.quantity)
                        t.order.transition_to(S.QUEUED, now, f"{st.station.name} went DOWN")
                        t.fill_started_tick = t.fill_done_tick = None
                        requeued += 1

            # 2. finish fills, ship verified orders
            for t in tracked.values():
                if t.order.status is S.VERIFIED and t.verified_tick is not None:
                    if t.verified_tick < tick:
                        t.order.transition_to(S.SHIPPED, now, "shipped")
                        t.shipped_tick = tick
                elif t.order.status is S.FILLING and t.fill_done_tick == tick:
                    scheduler.complete(t.order.id)
                    t.order.transition_to(S.VERIFIED, now, "verified", Role.PHARMACIST)
                    t.verified_tick = tick

            # 3. arrivals and validation
            for t in arrivals.get(tick, []):
                stock = inventory[t.order.drug.ndc]
                if stock.can_fulfill(t.order.quantity):
                    t.order.transition_to(S.VALIDATED, now, "stock check passed")
                    t.order.transition_to(S.QUEUED, now, "released to queue")
                    scheduler.enqueue(_entry(t.order))
                else:
                    t.order.transition_to(S.ON_HOLD, now, "insufficient stock")

            # 4. dispatch
            while assignments := scheduler.assign():
                for a in assignments:
                    t = tracked[a.order_id]
                    try:
                        inventory[t.order.drug.ndc].decrement(t.order.quantity)
                    except InsufficientStockError:
                        scheduler.complete(a.order_id)
                        t.order.transition_to(S.ON_HOLD, now, "insufficient stock at fill time")
                        continue
                    t.order.assigned_station = a.station_id
                    t.order.transition_to(S.FILLING, now, f"dispatched to station {a.station_id}")
                    t.fill_started_tick = tick
                    t.fill_done_tick = tick + FILL_TICKS[t.order.drug.dosage_form]

            # 5. utilization sample
            for st in floor.values():
                st.busy_slot_ticks += scheduler.station_load(st.station.id)

        return self._report(tracked, floor, requeued, failures, started)

    def _report(
        self,
        tracked: dict[int, _Tracked],
        floor: dict[int, _StationState],
        requeued: int,
        failures: int,
        started: float,
    ) -> SimulationReport:
        ticks = self.config.ticks
        status_counts = {s.value: 0 for s in OrderStatus}
        waits: dict[Priority, list[int]] = {p: [] for p in Priority}
        turnarounds: dict[Priority, list[int]] = {p: [] for p in Priority}
        on_time: dict[Priority, list[bool]] = {p: [] for p in Priority}

        for t in tracked.values():
            status_counts[t.order.status.value] += 1
            if t.fill_started_tick is not None:
                waits[t.order.priority].append(t.fill_started_tick - t.arrival_tick)
            if t.shipped_tick is not None:
                turnarounds[t.order.priority].append(t.shipped_tick - t.arrival_tick)
                shipped_at = SIM_START + timedelta(minutes=t.shipped_tick)
                on_time[t.order.priority].append(shipped_at <= t.order.due_by)

        usage = [
            StationUsage(
                station_id=st.station.id,
                name=st.station.name,
                capacity=st.station.capacity,
                busy_slot_ticks=st.busy_slot_ticks,
                down_ticks=st.down_ticks,
                utilization=round(st.busy_slot_ticks / (st.station.capacity * ticks), 4),
            )
            for st in floor.values()
        ]
        total_slots = sum(st.station.capacity for st in floor.values()) * ticks
        total_busy = sum(st.busy_slot_ticks for st in floor.values())

        return SimulationReport(
            config=self.config,
            orders_generated=len(tracked),
            orders_shipped=status_counts[S.SHIPPED.value],
            orders_on_hold=status_counts[S.ON_HOLD.value],
            status_counts=status_counts,
            average_wait_minutes={p.value: _mean(waits[p]) for p in Priority},
            average_turnaround_minutes={p.value: _mean(turnarounds[p]) for p in Priority},
            on_time_rate={p.value: _mean([float(x) for x in on_time[p]]) for p in Priority},
            station_utilization=usage,
            overall_utilization=round(total_busy / total_slots, 4) if total_slots else 0.0,
            requeued_after_station_down=requeued,
            station_failures=failures,
            audit_records=sum(len(t.order.history) for t in tracked.values()),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )


def _entry(order: Order) -> QueueEntry:
    return QueueEntry(
        order.id, order.priority, order.due_by, order.created_at, order.drug.dosage_form
    )


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None
