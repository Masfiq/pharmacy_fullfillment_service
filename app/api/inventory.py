from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ActorDep, SessionDep
from app.db.models import InventoryItemRow
from app.schemas import DrugOut, ErrorResponse, InventoryItemOut, RestockIn, RestockOut
from app.services.inventory import InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])


def to_out(item: InventoryItemRow) -> InventoryItemOut:
    return InventoryItemOut(
        drug=DrugOut.model_validate(item.drug),
        quantity_on_hand=item.quantity_on_hand,
        reorder_threshold=item.reorder_threshold,
        is_low_stock=item.quantity_on_hand < item.reorder_threshold,
        updated_at=item.updated_at,
    )


@router.get("", response_model=list[InventoryItemOut])
def list_inventory(session: SessionDep) -> list[InventoryItemOut]:
    return [to_out(i) for i in InventoryService(session).list_items()]


@router.get("/low-stock", response_model=list[InventoryItemOut])
def low_stock(session: SessionDep) -> list[InventoryItemOut]:
    """Items below their reorder threshold, lowest stock first."""
    return [to_out(i) for i in InventoryService(session).low_stock()]


@router.post("/{ndc}/restock", response_model=RestockOut, responses={404: {"model": ErrorResponse}})
def restock(ndc: str, body: RestockIn, session: SessionDep, actor: ActorDep) -> RestockOut:
    """Add stock. ON_HOLD orders for this drug that can now be filled go back to the queue."""
    item, released = InventoryService(session).restock(ndc, body.quantity, actor)
    return RestockOut(item=to_out(item), released_order_ids=released)
