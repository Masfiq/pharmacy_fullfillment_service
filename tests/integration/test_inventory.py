"""Inventory transactions: out of stock handling, restock, and race conditions."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import InventoryItemRow, OrderRow
from app.domain.enums import OrderStatus, Role
from app.services.inventory import InventoryService
from app.services.orders import NewOrder, OrderService
from tests.integration.factories import (
    TABLET_NDC,
    add_drug,
    add_station,
    advance,
    create_order,
    queued_order,
)

pytestmark = pytest.mark.integration


def stock(client: TestClient) -> int:
    value: int = client.get("/inventory").json()[0]["quantity_on_hand"]
    return value


def test_validation_with_insufficient_stock_goes_on_hold(client: TestClient, db: Session) -> None:
    add_drug(db, stock=20)
    order_id = create_order(client, quantity=30)["id"]

    body = advance(client, order_id)  # asked for VALIDATED

    assert body["status"] == "ON_HOLD"
    last = body["audit_log"][-1]
    assert (last["from_status"], last["to_status"]) == ("RECEIVED", "ON_HOLD")
    assert last["reason"] == "insufficient stock: need 30, have 20"
    assert last["actor"] == "SYSTEM"


def test_revalidating_while_still_short_is_409(client: TestClient, db: Session) -> None:
    add_drug(db, stock=20)
    order_id = create_order(client, quantity=30)["id"]
    advance(client, order_id)  # ON_HOLD

    response = client.post(f"/orders/{order_id}/transition")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "insufficient_stock"
    assert error["details"] == {"ndc": TABLET_NDC, "requested": 30, "on_hand": 20}
    detail = client.get(f"/orders/{order_id}").json()
    assert detail["status"] == "ON_HOLD"
    assert len(detail["audit_log"]) == 2  # no audit row for a change that did not happen


def test_stock_running_out_at_fill_time_holds_the_order(client: TestClient, db: Session) -> None:
    add_drug(db, stock=30)
    add_station(db, capacity=5)
    first = queued_order(client, quantity=30)["id"]
    second = queued_order(client, quantity=30)["id"]  # validated while 30 were still on hand

    assert advance(client, first)["status"] == "FILLING"
    held = advance(client, second)

    assert held["status"] == "ON_HOLD"
    assert held["audit_log"][-1]["reason"] == "insufficient stock at fill time"
    assert stock(client) == 0


def test_restock_releases_held_orders_by_priority_within_new_stock(
    client: TestClient, db: Session
) -> None:
    add_drug(db, stock=0, threshold=100)
    routine = create_order(client, quantity=60, priority="ROUTINE")["id"]
    stat = create_order(client, quantity=60, priority="STAT")["id"]
    urgent = create_order(client, quantity=60, priority="URGENT")["id"]
    for order_id in (routine, stat, urgent):
        assert advance(client, order_id)["status"] == "ON_HOLD"

    response = client.post(f"/inventory/{TABLET_NDC}/restock", json={"quantity": 130})

    assert response.status_code == 200
    body = response.json()
    assert body["released_order_ids"] == [stat, urgent]  # 120 of 130 units; routine needs 60 more
    assert body["item"]["quantity_on_hand"] == 130
    assert client.get(f"/orders/{routine}").json()["status"] == "ON_HOLD"
    stat_detail = client.get(f"/orders/{stat}").json()
    assert stat_detail["status"] == "QUEUED"
    assert [e["to_status"] for e in stat_detail["audit_log"][-2:]] == ["VALIDATED", "QUEUED"]


def test_low_stock_endpoint(client: TestClient, db: Session) -> None:
    add_drug(db, stock=50, threshold=100)
    add_drug(db, ndc="64980-438-01", name="Atenolol", strength="50 mg", stock=500, threshold=100)

    low = client.get("/inventory/low-stock").json()
    assert [i["drug"]["ndc"] for i in low] == [TABLET_NDC]
    assert low[0]["is_low_stock"] is True

    client.post(f"/inventory/{TABLET_NDC}/restock", json={"quantity": 100})
    assert client.get("/inventory/low-stock").json() == []


def test_restock_errors(client: TestClient, db: Session) -> None:
    add_drug(db)
    missing = client.post("/inventory/0000-0000-00/restock", json={"quantity": 5})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert client.post(f"/inventory/{TABLET_NDC}/restock", json={"quantity": 0}).status_code == 422


def test_database_rejects_negative_stock(db: Session) -> None:
    add_drug(db, stock=5)
    with pytest.raises(IntegrityError, match="non_negative_stock"):
        db.execute(update(InventoryItemRow).values(quantity_on_hand=-1))
        db.commit()
    db.rollback()


def test_concurrent_decrements_never_oversell(session_factory: sessionmaker[Session]) -> None:
    """20 threads race for stock that covers only 7 of them."""
    with session_factory() as session:
        add_drug(session, stock=70)

    barrier = threading.Barrier(20)

    def attempt(_: int) -> bool:
        with session_factory() as session:
            barrier.wait()  # release every thread at the same moment
            ok = InventoryService(session).try_decrement(TABLET_NDC, 10)
            session.commit()
            return ok

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))

    assert results.count(True) == 7
    with session_factory() as session:
        assert InventoryService(session).on_hand(TABLET_NDC) == 0


def test_concurrent_fills_respect_stock_and_station_capacity(
    session_factory: sessionmaker[Session],
) -> None:
    """12 queued orders filled concurrently: stock covers 8, stations hold 5."""
    with session_factory() as session:
        add_drug(session, stock=8 * 30)
        add_station(session, "Line A", capacity=3)
        add_station(session, "Line B", capacity=2)
        service = OrderService(session)
        order_ids = []
        for _ in range(12):
            order = service.create(NewOrder("PT-1", TABLET_NDC, 30))
            service.transition(order.id)  # VALIDATED
            service.transition(order.id)  # QUEUED
            order_ids.append(order.id)

    barrier = threading.Barrier(len(order_ids))

    def fill(order_id: int) -> str:
        with session_factory() as session:
            barrier.wait()
            try:
                return OrderService(session).transition(order_id, actor=Role.TECHNICIAN).status
            except Exception as exc:  # NoStationAvailableError once stations are full
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=len(order_ids)) as pool:
        outcomes = list(pool.map(fill, order_ids))

    assert outcomes.count(OrderStatus.FILLING) == 5  # station capacity, never more
    assert outcomes.count("NoStationAvailableError") == 7
    with session_factory() as session:
        filling = session.scalars(
            select(OrderRow).where(OrderRow.status == OrderStatus.FILLING)
        ).all()
        per_station: dict[int | None, int] = {}
        for o in filling:
            per_station[o.assigned_station_id] = per_station.get(o.assigned_station_id, 0) + 1
        assert sorted(per_station.values()) == [2, 3]
        assert InventoryService(session).on_hand(TABLET_NDC) == 240 - 5 * 30
