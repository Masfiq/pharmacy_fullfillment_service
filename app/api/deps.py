"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain.enums import Role

SessionDep = Annotated[Session, Depends(get_session)]


def get_actor(
    x_role: Annotated[
        Literal["TECHNICIAN", "PHARMACIST"],
        Header(description="Who is acting. Verifying a fill requires PHARMACIST."),
    ] = "TECHNICIAN",
) -> Role:
    return Role(x_role)


ActorDep = Annotated[Role, Depends(get_actor)]
