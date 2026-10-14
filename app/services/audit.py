"""The single place where an order's status changes.

Every status change goes through ``apply_transition``, which checks the state
machine and writes the audit row in the same transaction as the change. There
is no code path that updates ``orders.status`` without an audit entry.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditLogRow, OrderRow, utcnow
from app.domain import state_machine
from app.domain.enums import OrderStatus, Role


def apply_transition(
    session: Session,
    order: OrderRow,
    target: OrderStatus,
    reason: str,
    actor: Role,
) -> AuditLogRow:
    state_machine.ensure_transition(order.status, target)
    entry = AuditLogRow(
        order_id=order.id,
        from_status=order.status,
        to_status=target,
        timestamp=utcnow(),
        reason=reason,
        actor=actor,
    )
    order.status = target
    if target is OrderStatus.QUEUED:
        order.assigned_station_id = None
    session.add(entry)
    return entry
