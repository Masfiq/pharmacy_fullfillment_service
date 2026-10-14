from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import DosageForm, Priority, StationStatus
from app.domain.errors import ConflictError, NotFoundError
from app.domain.scheduler import Assignment, QueueEntry, Scheduler

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
TABLET = DosageForm.TABLET
LIQUID = DosageForm.LIQUID


def entry(
    order_id: int,
    priority: Priority = Priority.ROUTINE,
    due_in_minutes: int = 600,
    created_offset: int = 0,
    form: DosageForm = TABLET,
) -> QueueEntry:
    return QueueEntry(
        order_id=order_id,
        priority=priority,
        due_by=T0 + timedelta(minutes=due_in_minutes),
        created_at=T0 + timedelta(minutes=created_offset),
        dosage_form=form,
    )


def test_snapshot_orders_by_priority_then_due_by_then_created_at() -> None:
    s = Scheduler()
    s.enqueue(entry(1, Priority.ROUTINE, due_in_minutes=60))
    s.enqueue(entry(2, Priority.STAT, due_in_minutes=300))
    s.enqueue(entry(3, Priority.URGENT, due_in_minutes=30))
    s.enqueue(entry(4, Priority.STAT, due_in_minutes=100))
    s.enqueue(entry(5, Priority.STAT, due_in_minutes=100, created_offset=-5))

    assert [e.order_id for e in s.snapshot()] == [5, 4, 2, 3, 1]


def test_assign_respects_priority_and_capacity() -> None:
    s = Scheduler()
    s.add_station(10, capacity=2, supported_forms=[TABLET])
    s.enqueue(entry(1, Priority.ROUTINE))
    s.enqueue(entry(2, Priority.STAT))
    s.enqueue(entry(3, Priority.URGENT))

    assert s.assign() == [Assignment(2, 10), Assignment(3, 10)]
    assert [e.order_id for e in s.snapshot()] == [1]
    assert s.station_load(10) == 2
    assert s.assign() == []  # station is full


def test_order_stays_queued_when_no_compatible_station() -> None:
    s = Scheduler()
    s.add_station(10, capacity=5, supported_forms=[TABLET])
    s.enqueue(entry(1, Priority.STAT, form=LIQUID))
    s.enqueue(entry(2, Priority.ROUTINE, form=TABLET))

    # The STAT liquid order cannot be placed, but it must not block the tablet order.
    assert s.assign() == [Assignment(2, 10)]
    assert [e.order_id for e in s.snapshot()] == [1]
    assert 1 in s


def test_down_station_gets_no_work() -> None:
    s = Scheduler()
    s.add_station(10, capacity=5, supported_forms=[TABLET], status=StationStatus.DOWN)
    s.enqueue(entry(1))
    assert s.assign() == []
    s.mark_station_active(10)
    assert s.assign() == [Assignment(1, 10)]


def test_station_down_requeues_in_progress_orders_at_front() -> None:
    s = Scheduler()
    s.add_station(10, capacity=2, supported_forms=[TABLET])
    s.add_station(20, capacity=1, supported_forms=[TABLET])
    s.enqueue(entry(1, Priority.STAT))
    s.enqueue(entry(2, Priority.STAT))
    s.enqueue(entry(3, Priority.STAT))
    assert len(s.assign()) == 3
    s.enqueue(entry(4, Priority.ROUTINE))

    station_of_1 = s.station_of(1)
    assert station_of_1 is not None
    requeued = s.mark_station_down(station_of_1)

    assert 1 in requeued
    assert s.station_load(station_of_1) == 0
    # Requeued STAT work is ahead of the ROUTINE order that arrived later.
    assert [e.order_id for e in s.snapshot()][: len(requeued)] == sorted(requeued)
    assert s.snapshot()[-1].order_id == 4


def test_load_balances_to_station_with_most_free_capacity() -> None:
    s = Scheduler()
    s.add_station(1, capacity=1, supported_forms=[TABLET])
    s.add_station(2, capacity=3, supported_forms=[TABLET])
    s.enqueue(entry(1))
    assert s.assign() == [Assignment(1, 2)]


def test_complete_frees_capacity() -> None:
    s = Scheduler()
    s.add_station(10, capacity=1, supported_forms=[TABLET])
    s.enqueue(entry(1))
    s.enqueue(entry(2))
    assert s.assign() == [Assignment(1, 10)]
    s.complete(1)
    assert s.assign() == [Assignment(2, 10)]
    with pytest.raises(NotFoundError):
        s.complete(1)


def test_remove_is_lazy_and_skips_stale_entries() -> None:
    s = Scheduler()
    s.add_station(10, capacity=5, supported_forms=[TABLET])
    s.enqueue(entry(1, Priority.STAT))
    s.enqueue(entry(2))
    assert s.remove(1)
    assert not s.remove(1)
    assert len(s) == 1
    assert s.assign() == [Assignment(2, 10)]


def test_reenqueue_after_remove_uses_new_entry() -> None:
    s = Scheduler()
    s.add_station(10, capacity=5, supported_forms=[TABLET])
    s.enqueue(entry(1, Priority.ROUTINE))
    s.remove(1)
    s.enqueue(entry(1, Priority.STAT))
    s.enqueue(entry(2, Priority.URGENT))
    assert [a.order_id for a in s.assign()] == [1, 2]


def test_duplicate_enqueue_and_station_rejected() -> None:
    s = Scheduler()
    s.add_station(10, capacity=1, supported_forms=[TABLET])
    s.enqueue(entry(1))
    with pytest.raises(ConflictError):
        s.enqueue(entry(1))
    with pytest.raises(ConflictError):
        s.add_station(10, capacity=1, supported_forms=[TABLET])
    with pytest.raises(ValueError):
        s.add_station(11, capacity=0, supported_forms=[TABLET])
    with pytest.raises(NotFoundError):
        s.mark_station_down(999)


def test_add_station_restores_in_progress_orders() -> None:
    s = Scheduler()
    s.add_station(10, capacity=2, supported_forms=[TABLET], in_progress=[entry(1)])
    s.enqueue(entry(2))
    s.enqueue(entry(3))
    assert s.assign() == [Assignment(2, 10)]
    assert s.station_of(1) == 10


def test_mixed_forms_fill_every_compatible_slot() -> None:
    s = Scheduler()
    s.add_station(1, capacity=2, supported_forms=[TABLET, DosageForm.CAPSULE])
    s.add_station(2, capacity=1, supported_forms=[LIQUID])
    for i, form in enumerate([LIQUID, LIQUID, TABLET, DosageForm.CAPSULE, TABLET], start=1):
        s.enqueue(entry(i, created_offset=i, form=form))

    result = {a.order_id: a.station_id for a in s.assign()}
    assert result == {1: 2, 3: 1, 4: 1}
    assert [e.order_id for e in s.snapshot()] == [2, 5]
