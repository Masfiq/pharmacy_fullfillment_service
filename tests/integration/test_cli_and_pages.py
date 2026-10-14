import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import cli
from app.db import session as db_session
from tests.integration.factories import add_drug, add_station, advance, queued_order

pytestmark = pytest.mark.integration


def test_root_redirects_to_docs_and_dashboard_is_served(client: TestClient) -> None:
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/docs"
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Fulfillment floor" in page.text


def test_manual_requeue_from_filling_returns_stock(client: TestClient, db: Session) -> None:
    add_drug(db, stock=100)
    add_station(db)
    order_id = queued_order(client, quantity=30)["id"]
    advance(client, order_id)  # FILLING

    body = advance(client, order_id, to="QUEUED")

    assert body["status"] == "QUEUED"
    assert body["assigned_station_id"] is None
    assert body["audit_log"][-1]["reason"] == "returned to queue"
    assert client.get("/inventory").json()[0]["quantity_on_hand"] == 100


def test_cli_seed_and_simulate(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(db_session, "get_session_factory", lambda: session_factory)

    cli.main(["seed", "--orders", "5"])
    assert "Seeded" in capsys.readouterr().out
    cli.main(["seed"])
    assert "already seeded" in capsys.readouterr().out

    cli.main(["simulate", "--orders", "200", "--ticks", "120", "--seed", "1"])
    report = json.loads(capsys.readouterr().out)
    assert report["orders_generated"] == 200
    assert sum(report["status_counts"].values()) == 200
