"""Shared fixtures.

Integration tests run against a real PostgreSQL test database
(``TEST_DATABASE_URL``, default ``pharmacy_test`` on localhost). The schema is
built by running the real Alembic migrations, and every test starts from
empty tables.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.cli import migrate
from app.config import get_settings
from app.db.session import get_session, make_engine
from app.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", get_settings().test_database_url)
TABLES = "order_audit_log, orders, inventory_items, stations, drugs"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = make_engine(TEST_DATABASE_URL)
    try:
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as exc:  # pragma: no cover - depends on the environment
        pytest.fail(
            f"Cannot reach the test database at {TEST_DATABASE_URL}. "
            f"Start it with `docker compose up -d`. ({exc.orig})"
        )
    migrate(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
