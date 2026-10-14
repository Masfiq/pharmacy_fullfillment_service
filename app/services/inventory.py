"""Inventory use cases, including the race safe stock decrement."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.db.models import InventoryItemRow, OrderRow, utcnow
from app.domain.enums import OrderStatus, Role
from app.domain.errors import NotFoundError
from app.services.audit import apply_transition


class InventoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_items(self) -> Sequence[InventoryItemRow]:
        stmt = (
            select(InventoryItemRow)
            .options(joinedload(InventoryItemRow.drug))
            .order_by(InventoryItemRow.drug_ndc)
        )
        return self.session.scalars(stmt).all()

    def low_stock(self) -> Sequence[InventoryItemRow]:
        stmt = (
            select(InventoryItemRow)
            .options(joinedload(InventoryItemRow.drug))
            .where(InventoryItemRow.quantity_on_hand < InventoryItemRow.reorder_threshold)
            .order_by(InventoryItemRow.quantity_on_hand, InventoryItemRow.drug_ndc)
        )
        return self.session.scalars(stmt).all()

    def get(self, ndc: str) -> InventoryItemRow:
        stmt = (
            select(InventoryItemRow)
            .options(joinedload(InventoryItemRow.drug))
            .where(InventoryItemRow.drug_ndc == ndc)
        )
        item = self.session.scalars(stmt).one_or_none()
        if item is None:
            raise NotFoundError(f"No inventory item for NDC {ndc}", ndc=ndc)
        return item

    def on_hand(self, ndc: str) -> int:
        quantity = self.session.scalar(
            select(InventoryItemRow.quantity_on_hand).where(InventoryItemRow.drug_ndc == ndc)
        )
        return quantity or 0

    def try_decrement(self, ndc: str, quantity: int) -> bool:
        """Atomically remove stock. Returns False (and changes nothing) if short.

        This is one conditional UPDATE, not "read, check, then write":

            UPDATE inventory_items
               SET quantity_on_hand = quantity_on_hand - :qty
             WHERE drug_ndc = :ndc AND quantity_on_hand >= :qty

        PostgreSQL takes a row lock for the UPDATE. If two transactions race for
        the last units, the second one waits, then re-evaluates the WHERE clause
        against the committed value, matches zero rows, and we report "short".
        Stock can never be oversold, and the CHECK (quantity_on_hand >= 0)
        constraint backs this up at the schema level.
        """
        stmt = (
            update(InventoryItemRow)
            .where(
                InventoryItemRow.drug_ndc == ndc,
                InventoryItemRow.quantity_on_hand >= quantity,
            )
            .values(
                quantity_on_hand=InventoryItemRow.quantity_on_hand - quantity,
                updated_at=utcnow(),
            )
            .returning(InventoryItemRow.id)
        )
        return self.session.execute(stmt).first() is not None

    def give_back(self, ndc: str, quantity: int) -> None:
        """Return stock from a fill that was interrupted (station down or cancel)."""
        self.session.execute(
            update(InventoryItemRow)
            .where(InventoryItemRow.drug_ndc == ndc)
            .values(
                quantity_on_hand=InventoryItemRow.quantity_on_hand + quantity,
                updated_at=utcnow(),
            )
        )

    def restock(self, ndc: str, quantity: int, actor: Role) -> tuple[InventoryItemRow, list[int]]:
        """Add stock, then release ON_HOLD orders for this drug back into the queue.

        Held orders are released in priority order while the new stock covers
        their combined quantity, so a restock never releases more orders than
        it can actually fill. Returns the item and the released order ids.
        """
        item = self.get(ndc)
        self.give_back(ndc, quantity)
        self.session.flush()
        self.session.refresh(item)

        held = self.session.scalars(
            select(OrderRow)
            .where(OrderRow.drug_ndc == ndc, OrderRow.status == OrderStatus.ON_HOLD)
            .with_for_update(skip_locked=True)  # never wait on an order another request holds
        ).all()
        held = sorted(held, key=lambda o: (o.priority.rank, o.due_by, o.created_at, o.id))

        released: list[int] = []
        available = item.quantity_on_hand
        for order in held:
            if order.quantity > available:
                continue
            available -= order.quantity
            reason = f"restocked {quantity} units of {ndc}"
            apply_transition(self.session, order, OrderStatus.VALIDATED, reason, actor)
            apply_transition(self.session, order, OrderStatus.QUEUED, "released from hold", actor)
            released.append(order.id)

        self.session.commit()
        self.session.refresh(item)
        return item, released
