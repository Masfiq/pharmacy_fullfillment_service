from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ActorDep, SessionDep
from app.schemas import AssignmentOut, DispatchOut, OrderOut, QueueItemOut
from app.services.queue import QueueService

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("", response_model=list[QueueItemOut])
def get_queue(session: SessionDep) -> list[QueueItemOut]:
    """Queued orders in the order they will be assigned: priority, then due_by, then age."""
    orders = QueueService(session).queue()
    return [
        QueueItemOut(position=i, order=OrderOut.model_validate(o))
        for i, o in enumerate(orders, start=1)
    ]


@router.post("/dispatch", response_model=DispatchOut)
def dispatch(session: SessionDep, actor: ActorDep) -> DispatchOut:
    """Assign queued orders to free compatible stations and start filling them."""
    result = QueueService(session).dispatch(actor)
    return DispatchOut(
        assigned=[
            AssignmentOut(order_id=a.order_id, station_id=a.station_id) for a in result.assigned
        ],
        held_order_ids=result.held_order_ids,
        remaining_queued=result.remaining_queued,
    )
