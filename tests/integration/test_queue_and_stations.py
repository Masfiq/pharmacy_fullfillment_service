import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.enums import DosageForm, StationStatus
from tests.integration.factories import (
    LIQUID_NDC,
    add_drug,
    add_liquid,
    add_station,
    advance,
    queued_order,
)

pytestmark = pytest.mark.integration


def test_queue_is_in_priority_then_due_order(client: TestClient, db: Session) -> None:
    add_drug(db)
    routine = queued_order(client, priority="ROUTINE")["id"]
    stat_late = queued_order(client, priority="STAT", due_by="2030-01-01T12:00:00Z")["id"]
    urgent = queued_order(client, priority="URGENT")["id"]
    stat_early = queued_order(client, priority="STAT", due_by="2030-01-01T09:00:00Z")["id"]

    queue = client.get("/queue").json()

    assert [q["order"]["id"] for q in queue] == [stat_early, stat_late, urgent, routine]
    assert [q["position"] for q in queue] == [1, 2, 3, 4]


def test_dispatch_fills_by_priority_and_capacity(client: TestClient, db: Session) -> None:
    add_drug(db)
    station = add_station(db, capacity=2)
    routine = queued_order(client, priority="ROUTINE")["id"]
    stat = queued_order(client, priority="STAT")["id"]
    urgent = queued_order(client, priority="URGENT")["id"]

    result = client.post("/queue/dispatch").json()

    assert result["assigned"] == [
        {"order_id": stat, "station_id": station.id},
        {"order_id": urgent, "station_id": station.id},
    ]
    assert result["remaining_queued"] == 1
    assert [q["order"]["id"] for q in client.get("/queue").json()] == [routine]
    stations = client.get("/stations").json()
    assert stations[0]["current_load"] == 2
    assert client.get(f"/orders/{stat}").json()["audit_log"][-1]["reason"] == (
        f"dispatched to {station.name}"
    )


def test_dispatch_skips_orders_without_compatible_station(client: TestClient, db: Session) -> None:
    add_drug(db)
    add_liquid(db)
    add_station(db, capacity=5, forms=[DosageForm.TABLET])
    liquid = queued_order(client, ndc=LIQUID_NDC, quantity=150, priority="STAT")["id"]
    tablet = queued_order(client, priority="ROUTINE")["id"]

    result = client.post("/queue/dispatch").json()

    assert [a["order_id"] for a in result["assigned"]] == [tablet]
    assert client.get(f"/orders/{liquid}").json()["status"] == "QUEUED"


def test_dispatch_holds_orders_when_stock_runs_out(client: TestClient, db: Session) -> None:
    add_drug(db, stock=60)
    add_station(db, capacity=5)
    ids = [queued_order(client, quantity=30)["id"] for _ in range(3)]

    result = client.post("/queue/dispatch").json()

    assert [a["order_id"] for a in result["assigned"]] == ids[:2]
    assert result["held_order_ids"] == [ids[2]]
    assert result["remaining_queued"] == 0


def test_manual_fill_without_free_station_is_409(client: TestClient, db: Session) -> None:
    add_drug(db)
    add_station(db, capacity=1)
    first = queued_order(client)["id"]
    second = queued_order(client)["id"]
    advance(client, first)  # takes the only slot

    response = client.post(f"/orders/{second}/transition")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_station_available"
    assert client.get(f"/orders/{second}").json()["status"] == "QUEUED"


def test_station_down_requeues_its_orders_and_returns_stock(
    client: TestClient, db: Session
) -> None:
    add_drug(db, stock=1000)
    line_a = add_station(db, "Line A", capacity=2)
    line_b = add_station(db, "Line B", capacity=2, status=StationStatus.DOWN)
    ids = [queued_order(client, quantity=30)["id"] for _ in range(2)]
    client.post("/queue/dispatch")
    assert client.get("/inventory").json()[0]["quantity_on_hand"] == 940

    response = client.patch(f"/stations/{line_a.id}", json={"status": "DOWN"})

    assert response.status_code == 200
    body = response.json()
    assert body["requeued_order_ids"] == ids
    assert body["station"]["status"] == "DOWN"
    assert body["station"]["current_load"] == 0
    assert client.get("/inventory").json()[0]["quantity_on_hand"] == 1000
    detail = client.get(f"/orders/{ids[0]}").json()
    assert detail["status"] == "QUEUED"
    assert detail["assigned_station_id"] is None
    assert detail["audit_log"][-1]["reason"] == "station Line A went DOWN, returned to queue"

    # Nothing can be dispatched until another station comes up.
    assert client.post("/queue/dispatch").json()["assigned"] == []
    client.patch(f"/stations/{line_b.id}", json={"status": "ACTIVE"})
    reassigned = client.post("/queue/dispatch").json()["assigned"]
    assert {a["station_id"] for a in reassigned} == {line_b.id}


def test_station_patch_errors(client: TestClient, db: Session) -> None:
    station = add_station(db)
    missing = client.patch("/stations/999", json={"status": "DOWN"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    invalid = client.patch(f"/stations/{station.id}", json={"status": "BROKEN"})
    assert invalid.status_code == 422


def test_list_stations_shows_forms_and_load(client: TestClient, db: Session) -> None:
    add_station(db, "Liquids", capacity=3, forms=[DosageForm.LIQUID])
    stations = client.get("/stations").json()
    assert stations == [
        {
            "id": 1,
            "name": "Liquids",
            "capacity": 3,
            "supported_dosage_forms": ["LIQUID"],
            "status": "ACTIVE",
            "current_load": 0,
        }
    ]


def test_unknown_route_uses_error_shape(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
