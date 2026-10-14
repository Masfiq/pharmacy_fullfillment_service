from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionDep
from app.api.errors import error_response
from app.schemas import ErrorResponse, HealthOut, SimulateIn, SimulationReportOut
from app.services.snapshot import load_snapshot
from app.simulation import Simulation, SimulationConfig

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, responses={503: {"model": ErrorResponse}})
def health(session: SessionDep) -> HealthOut | JSONResponse:
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        return error_response(503, "database_unavailable", "Database is not reachable")
    return HealthOut(status="ok", database="ok")


@router.post(
    "/simulate", response_model=SimulationReportOut, responses={409: {"model": ErrorResponse}}
)
def simulate(
    session: SessionDep, body: SimulateIn | None = None
) -> SimulationReportOut | JSONResponse:
    """Run an in-memory simulation starting from the current drugs, stock, and stations.

    The simulation never writes to the database, so it is safe to run at any time.
    """
    body = body or SimulateIn()
    drugs, inventory, stations = load_snapshot(session)
    if not drugs or not stations:
        return error_response(409, "not_seeded", "Seed the database first: python -m app.cli seed")
    config = SimulationConfig(**body.model_dump())
    report = Simulation(drugs, inventory, stations, config).run()
    return SimulationReportOut.model_validate(asdict(report))
