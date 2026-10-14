from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from app.api.deps import ActorDep, SessionDep
from app.domain.enums import OrderStatus, Priority
from app.schemas import (
    CancelIn,
    ErrorResponse,
    OrderCreate,
    OrderDetailOut,
    OrderOut,
    OrderPage,
    TransitionIn,
)
from app.services.orders import NewOrder, OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "", response_model=OrderDetailOut, status_code=status.HTTP_201_CREATED, responses=ERRORS
)
def create_order(body: OrderCreate, session: SessionDep, actor: ActorDep) -> OrderDetailOut:
    order = OrderService(session).create(NewOrder(**body.model_dump()), actor)
    return OrderDetailOut.model_validate(order)


@router.get("", response_model=OrderPage, responses=ERRORS)
def list_orders(
    session: SessionDep,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    priority: Priority | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderPage:
    rows, total = OrderService(session).list_orders(status_filter, priority, limit, offset)
    return OrderPage(
        items=[OrderOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{order_id}", response_model=OrderDetailOut, responses=ERRORS)
def get_order(order_id: int, session: SessionDep) -> OrderDetailOut:
    return OrderDetailOut.model_validate(OrderService(session).get(order_id))


@router.post(
    "/{order_id}/transition",
    response_model=OrderDetailOut,
    responses={**ERRORS, 403: {"model": ErrorResponse}},
)
def transition_order(
    order_id: int, session: SessionDep, actor: ActorDep, body: TransitionIn | None = None
) -> OrderDetailOut:
    body = body or TransitionIn()
    order = OrderService(session).transition(order_id, body.to_status, body.reason, actor)
    return OrderDetailOut.model_validate(order)


@router.post("/{order_id}/cancel", response_model=OrderDetailOut, responses=ERRORS)
def cancel_order(
    order_id: int, session: SessionDep, actor: ActorDep, body: CancelIn | None = None
) -> OrderDetailOut:
    reason = body.reason if body else None
    return OrderDetailOut.model_validate(OrderService(session).cancel(order_id, reason, actor))
