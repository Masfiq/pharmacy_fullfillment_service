"""Pydantic request and response schemas (the public API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DosageForm, OrderStatus, Priority, Role, StationStatus, UnitType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----- errors ---------------------------------------------------------------


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


# ----- drugs and inventory -------------------------------------------------


class DrugOut(ORMModel):
    ndc: str
    name: str
    strength: str
    dosage_form: DosageForm
    unit_type: UnitType


class InventoryItemOut(ORMModel):
    drug: DrugOut
    quantity_on_hand: int
    reorder_threshold: int
    is_low_stock: bool
    updated_at: datetime


class RestockIn(BaseModel):
    quantity: int = Field(gt=0, le=1_000_000, examples=[500])


class RestockOut(BaseModel):
    item: InventoryItemOut
    released_order_ids: list[int]


# ----- stations ------------------------------------------------------------


class StationOut(ORMModel):
    id: int
    name: str
    capacity: int
    supported_dosage_forms: list[DosageForm]
    status: StationStatus
    current_load: int


class StationPatch(BaseModel):
    status: StationStatus


class StationPatchOut(BaseModel):
    station: StationOut
    requeued_order_ids: list[int]


# ----- orders --------------------------------------------------------------


class OrderCreate(BaseModel):
    patient_ref: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9\-]+$",
        description="Synthetic patient identifier. Never send real patient data.",
        examples=["PT-00012345"],
    )
    drug_ndc: str = Field(min_length=1, max_length=20, examples=["50090-7387-0"])
    quantity: int = Field(gt=0, le=10_000, examples=[30])
    priority: Priority = Priority.ROUTINE
    due_by: datetime | None = Field(
        default=None, description="Defaults to now plus the priority's turnaround target."
    )


class AuditEntryOut(ORMModel):
    from_status: OrderStatus | None
    to_status: OrderStatus
    timestamp: datetime
    reason: str
    actor: Role


class OrderOut(ORMModel):
    id: int
    patient_ref: str
    drug: DrugOut
    quantity: int
    priority: Priority
    due_by: datetime
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    assigned_station_id: int | None


class OrderDetailOut(OrderOut):
    audit_log: list[AuditEntryOut]


class OrderPage(BaseModel):
    items: list[OrderOut]
    total: int
    limit: int
    offset: int


class TransitionIn(BaseModel):
    to_status: OrderStatus | None = Field(
        default=None, description="Omit to move to the default next status."
    )
    reason: str | None = Field(default=None, max_length=500)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# ----- queue ---------------------------------------------------------------


class QueueItemOut(BaseModel):
    position: int
    order: OrderOut


class AssignmentOut(BaseModel):
    order_id: int
    station_id: int


class DispatchOut(BaseModel):
    assigned: list[AssignmentOut]
    held_order_ids: list[int]
    remaining_queued: int


# ----- simulation ----------------------------------------------------------


class SimulateIn(BaseModel):
    orders: int = Field(default=2000, ge=1, le=20_000)
    ticks: int = Field(default=480, ge=1, le=10_080, description="One tick is one minute.")
    seed: int | None = 42
    station_failure_rate: float = Field(default=0.002, ge=0.0, le=0.2)
    repair_ticks: int = Field(default=30, ge=1, le=1_440)


class StationUsageOut(BaseModel):
    station_id: int
    name: str
    capacity: int
    busy_slot_ticks: int
    down_ticks: int
    utilization: float


class SimulationReportOut(BaseModel):
    config: SimulateIn
    orders_generated: int
    orders_shipped: int
    orders_on_hold: int
    status_counts: dict[str, int]
    average_wait_minutes: dict[str, float | None]
    average_turnaround_minutes: dict[str, float | None]
    on_time_rate: dict[str, float | None]
    station_utilization: list[StationUsageOut]
    overall_utilization: float
    requeued_after_station_down: int
    station_failures: int
    audit_records: int
    elapsed_ms: float


class HealthOut(BaseModel):
    status: str
    database: str
