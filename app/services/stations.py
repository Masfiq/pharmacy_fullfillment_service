"""Station use cases, including returning work to the queue when a station fails."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OrderRow, StationRow
from app.domain.enums import OrderStatus, Role, StationStatus
from app.domain.errors import NotFoundError
from app.services.audit import apply_transition
from app.services.inventory import InventoryService


class StationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_stations(self) -> Sequence[StationRow]:
        return self.session.scalars(select(StationRow).order_by(StationRow.id)).all()

    def get(self, station_id: int, lock: bool = False) -> StationRow:
        stmt = select(StationRow).where(StationRow.id == station_id)
        if lock:
            stmt = stmt.with_for_update()
        station = self.session.scalars(stmt).one_or_none()
        if station is None:
            raise NotFoundError(f"Station {station_id} not found", station_id=station_id)
        return station

    def set_status(
        self, station_id: int, status: StationStatus, actor: Role
    ) -> tuple[StationRow, list[int]]:
        """Mark a station ACTIVE or DOWN. Returns the station and any requeued order ids.

        When a station goes DOWN, each order it was filling goes back to QUEUED
        and its stock is returned, because the partial fill cannot be trusted.
        The order keeps its priority, due_by, and created_at, so it goes back
        to the front of the line.
        """
        station = self.get(station_id, lock=True)
        requeued: list[int] = []
        if status is StationStatus.DOWN and station.status is StationStatus.ACTIVE:
            inventory = InventoryService(self.session)
            in_progress = self.session.scalars(
                select(OrderRow)
                .where(
                    OrderRow.assigned_station_id == station.id,
                    OrderRow.status == OrderStatus.FILLING,
                )
                .order_by(OrderRow.id)
                .with_for_update()
            ).all()
            for order in in_progress:
                inventory.give_back(order.drug_ndc, order.quantity)
                reason = f"station {station.name} went DOWN, returned to queue"
                apply_transition(self.session, order, OrderStatus.QUEUED, reason, actor)
                requeued.append(order.id)
        station.status = status
        self.session.commit()
        return station, requeued
