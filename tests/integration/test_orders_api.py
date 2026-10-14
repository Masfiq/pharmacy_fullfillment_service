from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import InventoryItemRow
from tests.integration.factories import (
    PHARMACIST,
    TABLET_NDC,
    add_drug,
    add_station,
    advance,
    create_order,
    queued_order,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def floor(db: Session) -> None:
    add_drug(db, stock=1000)
    add_station(db, capacity=2)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_openapi_lists_all_endpoints(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/orders",
        "/orders/{order_id}",
        "/orders/{order_id}/transition",
        "/orders/{order_id}/cancel",
        "/queue",
        "/queue/dispatch",
        "/stations",
        "/stations/{station_id}",
        "/inventory",
        "/inventory/low-stock",
        "/inventory/{ndc}/restock",
        "/simulate",
        "/health",
    } <= paths


def test_create_order_records_received_and_defaults_due_by(client: TestClient, floor: None) -> None:
    order = create_order(client, priority="STAT")

    assert order["status"] == "RECEIVED"
    assert order["drug"]["ndc"] == TABLET_NDC
    assert order["assigned_station_id"] is None
    created = datetime.fromisoformat(order["created_at"])
    due = datetime.fromisoformat(order["due_by"])
    assert due - created == timedelta(hours=1)  # STAT turnaround target
    assert [(e["from_status"], e["to_status"]) for e in order["audit_log"]] == [(None, "RECEIVED")]


def test_create_order_unknown_drug_is_404_with_error_shape(client: TestClient) -> None:
    response = client.post(
        "/orders", json={"patient_ref": "PT-1", "drug_ndc": "0000-0000-00", "quantity": 1}
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["details"] == {"ndc": "0000-0000-00"}


@pytest.mark.parametrize(
    "payload",
    [
        {"patient_ref": "PT-1", "drug_ndc": TABLET_NDC, "quantity": 0},
        {"patient_ref": "PT-1", "drug_ndc": TABLET_NDC, "quantity": 30, "priority": "ASAP"},
        {"patient_ref": "John Smith", "drug_ndc": TABLET_NDC, "quantity": 30},
        {"drug_ndc": TABLET_NDC, "quantity": 30},
    ],
)
def test_create_order_validation_errors(
    client: TestClient, floor: None, payload: dict[str, object]
) -> None:
    response = client.post("/orders", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_get_missing_order_is_404(client: TestClient) -> None:
    response = client.get("/orders/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_list_orders_filters_and_paginates(client: TestClient, floor: None) -> None:
    for priority in ["STAT", "ROUTINE", "ROUTINE", "URGENT", "ROUTINE"]:
        create_order(client, priority=priority)
    queued_order(client, priority="STAT")

    page = client.get("/orders", params={"priority": "ROUTINE", "limit": 2}).json()
    assert page["total"] == 3
    assert page["limit"] == 2
    assert len(page["items"]) == 2
    assert all(o["priority"] == "ROUTINE" for o in page["items"])

    second = client.get("/orders", params={"priority": "ROUTINE", "limit": 2, "offset": 2}).json()
    assert len(second["items"]) == 1

    queued = client.get("/orders", params={"status": "QUEUED"}).json()
    assert queued["total"] == 1
    assert queued["items"][0]["priority"] == "STAT"

    assert client.get("/orders", params={"limit": 0}).status_code == 422


def test_full_workflow_with_audit_trail(client: TestClient, db: Session, floor: None) -> None:
    order_id = create_order(client, quantity=30)["id"]

    assert advance(client, order_id)["status"] == "VALIDATED"
    assert advance(client, order_id)["status"] == "QUEUED"
    filling = advance(client, order_id)
    assert filling["status"] == "FILLING"
    assert filling["assigned_station_id"] is not None
    assert advance(client, order_id, headers=PHARMACIST)["status"] == "VERIFIED"
    shipped = advance(client, order_id)

    assert shipped["status"] == "SHIPPED"
    trail = [(e["from_status"], e["to_status"]) for e in shipped["audit_log"]]
    assert trail == [
        (None, "RECEIVED"),
        ("RECEIVED", "VALIDATED"),
        ("VALIDATED", "QUEUED"),
        ("QUEUED", "FILLING"),
        ("FILLING", "VERIFIED"),
        ("VERIFIED", "SHIPPED"),
    ]
    assert shipped["audit_log"][4]["actor"] == "PHARMACIST"
    assert all(e["reason"] for e in shipped["audit_log"])  # every step says why
    timestamps = [e["timestamp"] for e in shipped["audit_log"]]
    assert timestamps == sorted(timestamps)

    stock = db.get(InventoryItemRow, 1)
    assert stock is not None
    assert stock.quantity_on_hand == 1000 - 30  # decremented exactly once, at fill time


def test_only_pharmacist_can_verify(client: TestClient, floor: None) -> None:
    order_id = queued_order(client)["id"]
    advance(client, order_id)  # FILLING

    response = client.post(f"/orders/{order_id}/transition")  # default role: TECHNICIAN
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    assert client.get(f"/orders/{order_id}").json()["status"] == "FILLING"

    bad_role = client.post(f"/orders/{order_id}/transition", headers={"X-Role": "SYSTEM"})
    assert bad_role.status_code == 422


def test_invalid_transition_is_409_and_changes_nothing(client: TestClient, floor: None) -> None:
    order_id = create_order(client)["id"]

    response = client.post(f"/orders/{order_id}/transition", json={"to_status": "SHIPPED"})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"] == {"current_status": "RECEIVED", "requested_status": "SHIPPED"}
    detail = client.get(f"/orders/{order_id}").json()
    assert detail["status"] == "RECEIVED"
    assert len(detail["audit_log"]) == 1


def test_shipped_cannot_go_back_to_filling(client: TestClient, floor: None) -> None:
    order_id = queued_order(client)["id"]
    advance(client, order_id)
    advance(client, order_id, headers=PHARMACIST)
    advance(client, order_id)  # SHIPPED

    back = client.post(f"/orders/{order_id}/transition", json={"to_status": "FILLING"})
    assert back.status_code == 409
    assert "SHIPPED to FILLING" in back.json()["error"]["message"]

    nothing_next = client.post(f"/orders/{order_id}/transition")
    assert nothing_next.status_code == 409
    assert "final status" in nothing_next.json()["error"]["message"]

    cancel = client.post(f"/orders/{order_id}/cancel")
    assert cancel.status_code == 409


def test_cancel_queued_order_with_reason(client: TestClient, floor: None) -> None:
    order_id = queued_order(client)["id"]
    response = client.post(f"/orders/{order_id}/cancel", json={"reason": "prescriber request"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["audit_log"][-1]["reason"] == "prescriber request"
    assert client.get("/queue").json() == []


def test_cancel_while_filling_returns_stock(client: TestClient, db: Session, floor: None) -> None:
    order_id = queued_order(client, quantity=90)["id"]
    advance(client, order_id)  # FILLING takes 90 units
    assert client.get("/inventory").json()[0]["quantity_on_hand"] == 910

    client.post(f"/orders/{order_id}/cancel")

    assert client.get("/inventory").json()[0]["quantity_on_hand"] == 1000
    assert client.get("/stations").json()[0]["current_load"] == 0


def test_transition_missing_order_is_404(client: TestClient) -> None:
    assert client.post("/orders/12345/transition").status_code == 404
