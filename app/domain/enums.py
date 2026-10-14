"""Enumerations shared by every layer of the service."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum


class Priority(StrEnum):
    """Order priority. Lower ``rank`` means the order is filled sooner."""

    STAT = "STAT"
    URGENT = "URGENT"
    ROUTINE = "ROUTINE"

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK[self]

    @property
    def default_turnaround(self) -> timedelta:
        """Service level target used to compute ``due_by`` when a caller omits it."""
        return _PRIORITY_TURNAROUND[self]


_PRIORITY_RANK: dict[Priority, int] = {
    Priority.STAT: 0,
    Priority.URGENT: 1,
    Priority.ROUTINE: 2,
}

_PRIORITY_TURNAROUND: dict[Priority, timedelta] = {
    Priority.STAT: timedelta(hours=1),
    Priority.URGENT: timedelta(hours=4),
    Priority.ROUTINE: timedelta(hours=24),
}


class OrderStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    FILLING = "FILLING"
    VERIFIED = "VERIFIED"
    SHIPPED = "SHIPPED"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"


class StationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DOWN = "DOWN"


class UnitType(StrEnum):
    EACH = "EACH"  # tablets, capsules, inhaler devices
    ML = "ML"  # liquids and injectables
    G = "G"  # creams and ointments


class DosageForm(StrEnum):
    """Dosage form categories that decide which filling station can handle a drug.

    The FDA NDC Directory has dozens of detailed forms (for example
    "TABLET, FILM COATED"); ``app.domain.normalize`` maps them onto these.
    """

    TABLET = "TABLET"
    CAPSULE = "CAPSULE"
    LIQUID = "LIQUID"
    INJECTABLE = "INJECTABLE"
    TOPICAL = "TOPICAL"
    INHALER = "INHALER"
    OTHER = "OTHER"

    @property
    def unit_type(self) -> UnitType:
        return _FORM_UNIT[self]


_FORM_UNIT: dict[DosageForm, UnitType] = {
    DosageForm.TABLET: UnitType.EACH,
    DosageForm.CAPSULE: UnitType.EACH,
    DosageForm.LIQUID: UnitType.ML,
    DosageForm.INJECTABLE: UnitType.ML,
    DosageForm.TOPICAL: UnitType.G,
    DosageForm.INHALER: UnitType.EACH,
    DosageForm.OTHER: UnitType.EACH,
}


class Role(StrEnum):
    """Who performed an action. Only a pharmacist may verify a filled order."""

    TECHNICIAN = "TECHNICIAN"
    PHARMACIST = "PHARMACIST"
    SYSTEM = "SYSTEM"
