"""Synthetic data generation (Faker). No real patient data is ever used.

Drugs come from the real FDA NDC Directory snapshot in ``data/``; everything
about patients and orders is generated here. One seeded generator keeps the
seed script, the simulation, and the tests reproducible.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from faker import Faker

from app.domain.entities import Drug
from app.domain.enums import DosageForm, Priority

# Roughly what a central fill pharmacy sees: most work is routine refills.
PRIORITY_WEIGHTS: dict[Priority, float] = {
    Priority.STAT: 0.10,
    Priority.URGENT: 0.25,
    Priority.ROUTINE: 0.65,
}

# Typical dispensed quantities, in the drug's unit type (EACH, ML, or G).
TYPICAL_QUANTITIES: dict[DosageForm, tuple[int, ...]] = {
    DosageForm.TABLET: (30, 60, 90),
    DosageForm.CAPSULE: (30, 60, 90),
    DosageForm.LIQUID: (100, 150, 200, 300),
    DosageForm.INJECTABLE: (3, 9, 15),
    DosageForm.TOPICAL: (15, 30, 45, 60),
    DosageForm.INHALER: (1, 2, 3),
    DosageForm.OTHER: (1, 2),
}

# (min, max) starting stock per dosage form.
STOCK_RANGES: dict[DosageForm, tuple[int, int]] = {
    DosageForm.TABLET: (600, 6000),
    DosageForm.CAPSULE: (600, 6000),
    DosageForm.LIQUID: (1000, 8000),
    DosageForm.INJECTABLE: (60, 400),
    DosageForm.TOPICAL: (200, 1500),
    DosageForm.INHALER: (20, 200),
    DosageForm.OTHER: (20, 200),
}

LOW_STOCK_FRACTION = 0.12


@dataclass(frozen=True, slots=True)
class OrderSpec:
    patient_ref: str
    drug: Drug
    quantity: int
    priority: Priority


@dataclass(frozen=True, slots=True)
class StockLevel:
    quantity_on_hand: int
    reorder_threshold: int


class SyntheticDataGenerator:
    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.faker = Faker()
        if seed is not None:
            self.faker.seed_instance(seed)

    def patient_ref(self) -> str:
        """A synthetic patient identifier such as ``PT-48213907``. Not a real person."""
        return str(self.faker.bothify("PT-########"))

    def priority(self) -> Priority:
        priorities = list(PRIORITY_WEIGHTS)
        return self.rng.choices(priorities, weights=list(PRIORITY_WEIGHTS.values()))[0]

    def quantity(self, form: DosageForm) -> int:
        return self.rng.choice(TYPICAL_QUANTITIES[form])

    def order(self, drugs: Sequence[Drug]) -> OrderSpec:
        drug = self.rng.choice(drugs)
        return OrderSpec(
            patient_ref=self.patient_ref(),
            drug=drug,
            quantity=self.quantity(drug.dosage_form),
            priority=self.priority(),
        )

    def stock_level(self, form: DosageForm) -> StockLevel:
        """Starting stock. About 12 percent of items start below their reorder threshold."""
        low, high = STOCK_RANGES[form]
        threshold = max(low // 2, 1)
        if self.rng.random() < LOW_STOCK_FRACTION:
            quantity = self.rng.randint(0, threshold - 1) if threshold > 1 else 0
        else:
            quantity = self.rng.randint(low, high)
        return StockLevel(quantity_on_hand=quantity, reorder_threshold=threshold)
