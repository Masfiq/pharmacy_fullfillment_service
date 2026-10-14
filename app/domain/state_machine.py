"""The order workflow as an explicit, table driven state machine.

Happy path: RECEIVED -> VALIDATED -> QUEUED -> FILLING -> VERIFIED -> SHIPPED

Side paths:
* ON_HOLD: stock was insufficient at validation or at fill time. A restock
  lets the order be validated again.
* FILLING -> QUEUED: the station went DOWN mid fill, so the order goes back.
* CANCELLED: allowed until the order has been verified by a pharmacist.

Keeping the rules in one table (instead of ``if`` checks spread across the
code) means the API, the services, and the simulation all enforce exactly the
same workflow, and the table doubles as documentation.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.enums import OrderStatus
from app.domain.errors import InvalidTransitionError

S = OrderStatus

ALLOWED_TRANSITIONS: Mapping[OrderStatus, frozenset[OrderStatus]] = MappingProxyType(
    {
        S.RECEIVED: frozenset({S.VALIDATED, S.ON_HOLD, S.CANCELLED}),
        S.VALIDATED: frozenset({S.QUEUED, S.CANCELLED}),
        S.QUEUED: frozenset({S.FILLING, S.ON_HOLD, S.CANCELLED}),
        S.FILLING: frozenset({S.VERIFIED, S.QUEUED, S.CANCELLED}),
        S.VERIFIED: frozenset({S.SHIPPED}),
        S.ON_HOLD: frozenset({S.VALIDATED, S.CANCELLED}),
        S.SHIPPED: frozenset(),
        S.CANCELLED: frozenset(),
    }
)

# The status an order moves to when a caller simply asks for "the next step".
DEFAULT_NEXT: Mapping[OrderStatus, OrderStatus] = MappingProxyType(
    {
        S.RECEIVED: S.VALIDATED,
        S.VALIDATED: S.QUEUED,
        S.QUEUED: S.FILLING,
        S.FILLING: S.VERIFIED,
        S.VERIFIED: S.SHIPPED,
        S.ON_HOLD: S.VALIDATED,
    }
)

TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    status for status, targets in ALLOWED_TRANSITIONS.items() if not targets
)


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Raise ``InvalidTransitionError`` unless ``current -> target`` is allowed."""
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def next_status(current: OrderStatus) -> OrderStatus:
    """The default next status, or ``InvalidTransitionError`` for terminal states."""
    try:
        return DEFAULT_NEXT[current]
    except KeyError:
        raise InvalidTransitionError(current, None) from None


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL_STATUSES
