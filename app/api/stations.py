from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ActorDep, SessionDep
from app.db.models import StationRow
from app.schemas import ErrorResponse, StationOut, StationPatch, StationPatchOut
from app.services.queue import QueueService
from app.services.stations import StationService

router = APIRouter(prefix="/stations", tags=["stations"])


def to_out(station: StationRow, loads: dict[int, int]) -> StationOut:
    return StationOut(
        id=station.id,
        name=station.name,
        capacity=station.capacity,
        supported_dosage_forms=sorted(station.dosage_forms),
        status=station.status,
        current_load=loads.get(station.id, 0),
    )


@router.get("", response_model=list[StationOut])
def list_stations(session: SessionDep) -> list[StationOut]:
    loads = QueueService(session).station_loads()
    return [to_out(s, loads) for s in StationService(session).list_stations()]


@router.patch(
    "/{station_id}", response_model=StationPatchOut, responses={404: {"model": ErrorResponse}}
)
def update_station(
    station_id: int, body: StationPatch, session: SessionDep, actor: ActorDep
) -> StationPatchOut:
    """Mark a station DOWN or ACTIVE. Going DOWN returns its in progress orders to the queue."""
    station, requeued = StationService(session).set_status(station_id, body.status, actor)
    loads = QueueService(session).station_loads()
    return StationPatchOut(station=to_out(station, loads), requeued_order_ids=requeued)
