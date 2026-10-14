"""Domain errors. The API layer maps each one to an HTTP status code."""

from __future__ import annotations

from typing import Any

from app.domain.enums import OrderStatus


class DomainError(Exception):
    """Base class. ``code`` is a stable machine readable identifier."""

    code: str = "domain_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    code = "not_found"


class InvalidTransitionError(DomainError):
    code = "invalid_transition"

    def __init__(self, current: OrderStatus, target: OrderStatus | None) -> None:
        if target is None:
            message = f"Order is {current.value}, which is a final status"
        else:
            message = f"Cannot move order from {current.value} to {target.value}"
        super().__init__(
            message,
            current_status=current.value,
            requested_status=target.value if target else None,
        )
        self.current = current
        self.target = target


class InsufficientStockError(DomainError):
    code = "insufficient_stock"


class NoStationAvailableError(DomainError):
    code = "no_station_available"


class PermissionDeniedError(DomainError):
    code = "permission_denied"


class ConflictError(DomainError):
    code = "conflict"
