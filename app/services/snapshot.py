"""Read the current database state into domain objects (used by the simulation)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import DrugRow, InventoryItemRow, StationRow
from app.domain.entities import Drug, InventoryItem, Station


def to_drug(row: DrugRow) -> Drug:
    return Drug(row.ndc, row.name, row.strength, row.dosage_form, row.unit_type)


def load_snapshot(session: Session) -> tuple[list[Drug], dict[str, InventoryItem], list[Station]]:
    items = session.scalars(
        select(InventoryItemRow).options(joinedload(InventoryItemRow.drug))
    ).all()
    drugs = [to_drug(i.drug) for i in items]
    inventory = {
        i.drug_ndc: InventoryItem(to_drug(i.drug), i.quantity_on_hand, i.reorder_threshold)
        for i in items
    }
    stations = [
        Station(s.id, s.name, s.capacity, s.dosage_forms, s.status)
        for s in session.scalars(select(StationRow).order_by(StationRow.id)).all()
    ]
    return drugs, inventory, stations
