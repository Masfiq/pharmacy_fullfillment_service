"""Load the real drug catalog, synthetic stock levels, stations, and synthetic orders."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.catalog import DEFAULT_STATIONS, load_drugs
from app.db.models import DrugRow, InventoryItemRow, StationRow
from app.domain.enums import Role
from app.domain.synthetic import SyntheticDataGenerator
from app.services.orders import NewOrder, OrderService

TABLES = "order_audit_log, orders, inventory_items, stations, drugs"


@dataclass(frozen=True, slots=True)
class SeedResult:
    drugs: int
    stations: int
    orders: int
    skipped: bool = False


def is_seeded(session: Session) -> bool:
    return session.scalar(select(DrugRow.ndc).limit(1)) is not None


def reset(session: Session) -> None:
    session.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    session.commit()


def seed(
    session: Session, orders: int = 40, seed_value: int = 42, force: bool = False
) -> SeedResult:
    if is_seeded(session) and not force:
        return SeedResult(0, 0, 0, skipped=True)
    if force:
        reset(session)

    gen = SyntheticDataGenerator(seed_value)
    drugs = load_drugs()
    for drug in drugs:
        session.add(
            DrugRow(
                ndc=drug.ndc,
                name=drug.name,
                strength=drug.strength,
                dosage_form=drug.dosage_form,
                unit_type=drug.unit_type,
            )
        )
        level = gen.stock_level(drug.dosage_form)
        session.add(
            InventoryItemRow(
                drug_ndc=drug.ndc,
                quantity_on_hand=level.quantity_on_hand,
                reorder_threshold=level.reorder_threshold,
            )
        )
    for spec in DEFAULT_STATIONS:
        session.add(
            StationRow(
                name=spec.name,
                capacity=spec.capacity,
                supported_dosage_forms=[f.value for f in spec.supported_dosage_forms],
            )
        )
    session.commit()

    service = OrderService(session)
    for _ in range(orders):
        o = gen.order(drugs)
        service.create(NewOrder(o.patient_ref, o.drug.ndc, o.quantity, o.priority), Role.SYSTEM)
    return SeedResult(len(drugs), len(DEFAULT_STATIONS), orders)
