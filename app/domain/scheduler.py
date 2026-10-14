"""Priority queue scheduler that assigns queued orders to filling stations.

This class is pure Python: it holds no database session and does no I/O.
The service layer builds one from the current database state, asks it for
assignments, and persists the result. The simulation keeps one alive across
ticks. Because it is pure, every rule is easy to unit test.

Ordering
    Orders are kept in a binary heap (``heapq``) keyed by
    ``(priority.rank, due_by, created_at, order_id)``. STAT beats URGENT beats
    ROUTINE; within a priority the earliest deadline wins; ``created_at`` and
    then ``order_id`` break ties so the order is always deterministic.

Complexity (n = queued orders, s = stations)
    enqueue: O(log n), one ``heappush``.
    remove:  O(1), lazy deletion; the stale heap entry is skipped when popped.
    assign:  O(k * (log n + s)) where k is the number of heap entries popped.
             Each pop is O(log n) and finding a compatible station scans the
             s stations. Orders that cannot be placed (no free compatible
             station) are set aside and pushed back afterwards, so one stuck
             liquid order never blocks tablet orders behind it. The loop stops
             as soon as no station has free capacity, so in the common case
             k is close to the number of free slots, not n.
    snapshot (queue listing): O(n log n), a sort of the live entries.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import DosageForm, Priority, StationStatus
from app.domain.errors import ConflictError, NotFoundError

SortKey = tuple[int, datetime, datetime, int]


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """What the scheduler needs to know about an order, and nothing more."""

    order_id: int
    priority: Priority
    due_by: datetime
    created_at: datetime
    dosage_form: DosageForm

    @property
    def sort_key(self) -> SortKey:
        return (self.priority.rank, self.due_by, self.created_at, self.order_id)


@dataclass(frozen=True, slots=True)
class Assignment:
    order_id: int
    station_id: int


@dataclass(slots=True)
class StationSlot:
    """The scheduler's view of a station: capacity and what it is filling now."""

    station_id: int
    capacity: int
    supported_forms: frozenset[DosageForm]
    status: StationStatus = StationStatus.ACTIVE
    in_progress: dict[int, QueueEntry] = field(default_factory=dict)

    @property
    def free_capacity(self) -> int:
        if self.status is not StationStatus.ACTIVE:
            return 0
        return max(self.capacity - len(self.in_progress), 0)

    def can_accept(self, form: DosageForm) -> bool:
        return self.free_capacity > 0 and form in self.supported_forms


class Scheduler:
    def __init__(self) -> None:
        self._heap: list[tuple[SortKey, QueueEntry]] = []
        self._queued: dict[int, QueueEntry] = {}
        self._stations: dict[int, StationSlot] = {}
        self._order_station: dict[int, int] = {}

    # ----- stations -------------------------------------------------------

    def add_station(
        self,
        station_id: int,
        capacity: int,
        supported_forms: Iterable[DosageForm],
        status: StationStatus = StationStatus.ACTIVE,
        in_progress: Iterable[QueueEntry] = (),
    ) -> None:
        """Register a station. ``in_progress`` restores orders already being filled."""
        if station_id in self._stations:
            raise ConflictError(f"Station {station_id} already registered")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        slot = StationSlot(station_id, capacity, frozenset(supported_forms), status)
        self._stations[station_id] = slot
        for entry in in_progress:
            slot.in_progress[entry.order_id] = entry
            self._order_station[entry.order_id] = station_id

    def mark_station_down(self, station_id: int) -> list[int]:
        """Take a station offline and put its in progress orders back in the queue.

        Returns the ids of the requeued orders. They keep their original
        priority, deadline, and creation time, so they return to the front of
        the line instead of the back.
        """
        slot = self._station(station_id)
        slot.status = StationStatus.DOWN
        requeued = list(slot.in_progress.values())
        slot.in_progress.clear()
        for entry in requeued:
            del self._order_station[entry.order_id]
            self.enqueue(entry)
        return [entry.order_id for entry in requeued]

    def mark_station_active(self, station_id: int) -> None:
        self._station(station_id).status = StationStatus.ACTIVE

    def station_load(self, station_id: int) -> int:
        return len(self._station(station_id).in_progress)

    def station_of(self, order_id: int) -> int | None:
        return self._order_station.get(order_id)

    # ----- queue ----------------------------------------------------------

    def enqueue(self, entry: QueueEntry) -> None:
        """Add an order to the queue. O(log n)."""
        if entry.order_id in self._queued or entry.order_id in self._order_station:
            raise ConflictError(f"Order {entry.order_id} is already scheduled")
        self._queued[entry.order_id] = entry
        heapq.heappush(self._heap, (entry.sort_key, entry))

    def remove(self, order_id: int) -> bool:
        """Drop a queued order (for example, it was cancelled). O(1), lazy."""
        return self._queued.pop(order_id, None) is not None

    def __len__(self) -> int:
        return len(self._queued)

    def __contains__(self, order_id: object) -> bool:
        return order_id in self._queued

    def snapshot(self) -> list[QueueEntry]:
        """Queued orders in the exact order they would be assigned. O(n log n)."""
        return sorted(self._queued.values(), key=lambda entry: entry.sort_key)

    # ----- assignment -----------------------------------------------------

    def assign(self) -> list[Assignment]:
        """Assign as many queued orders as current free capacity allows."""
        assignments: list[Assignment] = []
        set_aside: list[tuple[SortKey, QueueEntry]] = []

        while self._heap:
            open_forms = self._forms_with_free_capacity()
            if not open_forms:
                break
            key, entry = heapq.heappop(self._heap)
            if self._queued.get(entry.order_id) is not entry:
                continue  # stale entry left behind by remove()
            if entry.dosage_form not in open_forms:
                set_aside.append((key, entry))
                continue
            slot = self._best_station_for(entry.dosage_form)
            del self._queued[entry.order_id]
            slot.in_progress[entry.order_id] = entry
            self._order_station[entry.order_id] = slot.station_id
            assignments.append(Assignment(entry.order_id, slot.station_id))

        for item in set_aside:
            heapq.heappush(self._heap, item)
        return assignments

    def complete(self, order_id: int) -> None:
        """Free the station slot held by ``order_id`` (fill finished or aborted)."""
        station_id = self._order_station.pop(order_id, None)
        if station_id is None:
            raise NotFoundError(f"Order {order_id} is not in progress at any station")
        del self._stations[station_id].in_progress[order_id]

    # ----- helpers --------------------------------------------------------

    def _station(self, station_id: int) -> StationSlot:
        try:
            return self._stations[station_id]
        except KeyError:
            raise NotFoundError(f"Station {station_id} not found") from None

    def _forms_with_free_capacity(self) -> set[DosageForm]:
        forms: set[DosageForm] = set()
        for slot in self._stations.values():
            if slot.free_capacity > 0:
                forms.update(slot.supported_forms)
        return forms

    def _best_station_for(self, form: DosageForm) -> StationSlot:
        """The compatible station with the most free capacity (simple load balancing)."""
        candidates = [slot for slot in self._stations.values() if slot.can_accept(form)]
        return max(candidates, key=lambda slot: (slot.free_capacity, -slot.station_id))
