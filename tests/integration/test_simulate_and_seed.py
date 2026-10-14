import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.catalog import DEFAULT_STATIONS, load_drugs
from app.db.models import AuditLogRow, Base, DrugRow, OrderRow, StationRow
from app.seed import seed

pytestmark = pytest.mark.integration


def test_seed_loads_real_catalog_and_synthetic_orders(db: Session) -> None:
    result = seed(db, orders=25, seed_value=1)

    assert result.drugs == len(load_drugs())
    assert db.scalar(select(func.count()).select_from(DrugRow)) == result.drugs
    assert db.scalar(select(func.count()).select_from(StationRow)) == len(DEFAULT_STATIONS)
    orders = db.scalars(select(OrderRow)).all()
    assert len(orders) == 25
    assert all(o.patient_ref.startswith("PT-") for o in orders)
    assert db.scalar(select(func.count()).select_from(AuditLogRow)) == 25  # one RECEIVED each


def test_seed_is_idempotent_unless_forced(db: Session) -> None:
    seed(db, orders=5)
    assert seed(db, orders=5).skipped
    again = seed(db, orders=3, force=True)
    assert not again.skipped
    assert db.scalar(select(func.count()).select_from(OrderRow)) == 3


def test_simulate_endpoint_reports_without_touching_the_database(
    client: TestClient, db: Session
) -> None:
    seed(db, orders=10)
    before = client.get("/inventory").json()

    response = client.post("/simulate", json={"orders": 300, "ticks": 240, "seed": 3})

    assert response.status_code == 200
    report = response.json()
    assert report["orders_generated"] == 300
    assert sum(report["status_counts"].values()) == 300
    assert set(report["average_wait_minutes"]) == {"STAT", "URGENT", "ROUTINE"}
    assert len(report["station_utilization"]) == len(DEFAULT_STATIONS)
    assert client.get("/inventory").json() == before
    assert client.get("/orders").json()["total"] == 10


def test_simulate_requires_seed_and_valid_input(client: TestClient) -> None:
    empty = client.post("/simulate")
    assert empty.status_code == 409
    assert empty.json()["error"]["code"] == "not_seeded"
    assert client.post("/simulate", json={"orders": 0}).status_code == 422


def test_migrations_match_models(engine: Engine) -> None:
    """Fails if someone changes a model without writing a migration."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
