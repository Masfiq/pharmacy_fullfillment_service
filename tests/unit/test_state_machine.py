from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.domain import state_machine
from app.domain.entities import Drug, Order
from app.domain.enums import DosageForm, OrderStatus, Priority, Role, UnitType
from app.domain.errors import InvalidTransitionError

S = OrderStatus
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
DRUG = Drug("0071-0155-23", "Atorvastatin Calcium", "10 mg", DosageForm.TABLET, UnitType.EACH)

HAPPY_PATH = [S.RECEIVED, S.VALIDATED, S.QUEUED, S.FILLING, S.VERIFIED, S.SHIPPED]


def make_order() -> Order:
    return Order(
        id=1,
        patient_ref="PAT-00001",
        drug=DRUG,
        quantity=30,
        priority=Priority.ROUTINE,
        due_by=NOW + timedelta(hours=24),
        created_at=NOW,
    )


def test_every_status_has_a_transition_entry() -> None:
    assert set(state_machine.ALLOWED_TRANSITIONS) == set(OrderStatus)


@pytest.mark.parametrize(("current", "target"), list(pairwise(HAPPY_PATH)))
def test_happy_path_transitions_are_allowed(current: OrderStatus, target: OrderStatus) -> None:
    assert state_machine.can_transition(current, target)
    assert state_machine.next_status(current) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.SHIPPED, S.FILLING),
        (S.SHIPPED, S.CANCELLED),
        (S.RECEIVED, S.FILLING),
        (S.RECEIVED, S.SHIPPED),
        (S.QUEUED, S.VERIFIED),
        (S.VERIFIED, S.CANCELLED),
        (S.CANCELLED, S.RECEIVED),
        (S.ON_HOLD, S.FILLING),
    ],
)
def test_invalid_transitions_raise(current: OrderStatus, target: OrderStatus) -> None:
    with pytest.raises(InvalidTransitionError) as exc:
        state_machine.ensure_transition(current, target)
    assert exc.value.current is current
    assert exc.value.target is target
    assert current.value in exc.value.message and target.value in exc.value.message


def test_terminal_statuses() -> None:
    assert state_machine.TERMINAL_STATUSES == {S.SHIPPED, S.CANCELLED}
    assert state_machine.is_terminal(S.SHIPPED)
    assert not state_machine.is_terminal(S.FILLING)


def test_next_status_of_terminal_state_raises_clear_error() -> None:
    with pytest.raises(InvalidTransitionError, match="final status"):
        state_machine.next_status(S.SHIPPED)


def test_station_down_path_and_hold_release_path() -> None:
    assert state_machine.can_transition(S.FILLING, S.QUEUED)
    assert state_machine.can_transition(S.ON_HOLD, S.VALIDATED)
    assert state_machine.next_status(S.ON_HOLD) is S.VALIDATED


def test_order_walks_happy_path_and_records_history() -> None:
    order = make_order()
    for target in HAPPY_PATH[1:]:
        order.transition_to(target, NOW, reason=f"to {target}", actor=Role.TECHNICIAN)

    assert order.status is S.SHIPPED
    assert [r.to_status for r in order.history] == HAPPY_PATH[1:]
    assert [r.from_status for r in order.history] == HAPPY_PATH[:-1]
    assert all(r.order_id == 1 for r in order.history)


def test_order_rejects_invalid_transition_without_changing_state() -> None:
    order = make_order()
    with pytest.raises(InvalidTransitionError):
        order.transition_to(S.SHIPPED, NOW)
    assert order.status is S.RECEIVED
    assert order.history == []


def test_requeue_clears_assigned_station() -> None:
    order = make_order()
    for target in (S.VALIDATED, S.QUEUED, S.FILLING):
        order.transition_to(target, NOW)
    order.assigned_station = 7
    order.transition_to(S.QUEUED, NOW, reason="station down")
    assert order.assigned_station is None
