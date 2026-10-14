"""Small helpers to put known rows in the test database.

NDCs and names below are real products from the FDA NDC Directory snapshot
in ``data/drugs_openfda.csv``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import DrugRow, InventoryItemRow, StationRow
from app.domain.enums import DosageForm, StationStatus

TABLET_NDC = "50090-7387-0"  # Atorvastatin Calcium 20 mg tablet
LIQUID_NDC = "67296-2338-9"  # Albuterol Sulfate .83 mg/mL solution

PHARMACIST = {"X-Role": "PHARMACIST"}


def add_drug(
    session: Session,
    ndc: str = TABLET_NDC,
    name: str = "Atorvastatin Calcium",
    strength: str = "20 mg",
    form: DosageForm = DosageForm.TABLET,
    stock: int = 1000,
    threshold: int = 100,
) -> DrugRow:
    drug = DrugRow(
        ndc=ndc, name=name, strength=strength, dosage_form=form, unit_type=form.unit_type
    )
    session.add(drug)
    session.add(InventoryItemRow(drug_ndc=ndc, quantity_on_hand=stock, reorder_threshold=threshold))
    session.commit()
    return drug


def add_liquid(session: Session, stock: int = 5000, threshold: int = 500) -> DrugRow:
    return add_drug(
        session, LIQUID_NDC, "Albuterol Sulfate", ".83 mg/mL", DosageForm.LIQUID, stock, threshold
    )


def add_station(
    session: Session,
    name: str = "Tablet Line A",
    capacity: int = 2,
    forms: Iterable[DosageForm] = (DosageForm.TABLET, DosageForm.CAPSULE),
    status: StationStatus = StationStatus.ACTIVE,
) -> StationRow:
    station = StationRow(
        name=name,
        capacity=capacity,
        supported_dosage_forms=[f.value for f in forms],
        status=status,
    )
    session.add(station)
    session.commit()
    return station


def create_order(
    client: TestClient,
    ndc: str = TABLET_NDC,
    quantity: int = 30,
    priority: str = "ROUTINE",
    **extra: Any,
) -> dict[str, Any]:
    response = client.post(
        "/orders",
        json={
            "patient_ref": "PT-00000001",
            "drug_ndc": ndc,
            "quantity": quantity,
            "priority": priority,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def advance(
    client: TestClient, order_id: int, to: str | None = None, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    payload = {"to_status": to} if to else None
    response = client.post(f"/orders/{order_id}/transition", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def queued_order(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    order = create_order(client, **kwargs)
    advance(client, order["id"])  # VALIDATED
    return advance(client, order["id"])  # QUEUED
