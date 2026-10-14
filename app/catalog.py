"""Load the real drug catalog snapshot (FDA NDC Directory) and the station layout."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from app.domain.entities import Drug
from app.domain.enums import DosageForm, UnitType

DRUG_CSV = Path(__file__).resolve().parent.parent / "data" / "drugs_openfda.csv"


def load_drugs(path: Path = DRUG_CSV) -> list[Drug]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Drug(
                ndc=row["ndc"],
                name=row["name"],
                strength=row["strength"],
                dosage_form=DosageForm(row["dosage_form"]),
                unit_type=UnitType(row["unit_type"]),
            )
            for row in csv.DictReader(handle)
        ]


@dataclass(frozen=True, slots=True)
class StationSpec:
    name: str
    capacity: int
    supported_dosage_forms: tuple[DosageForm, ...]


F = DosageForm

# A small central fill floor: two robotic count lines for solid oral doses,
# dedicated benches for liquids, sterile products, and topicals or inhalers,
# plus a manual bench that can fill anything but has little capacity.
DEFAULT_STATIONS: tuple[StationSpec, ...] = (
    StationSpec("Robotic Tablet Line A", 6, (F.TABLET, F.CAPSULE)),
    StationSpec("Robotic Tablet Line B", 6, (F.TABLET, F.CAPSULE)),
    StationSpec("Liquids Bench", 2, (F.LIQUID,)),
    StationSpec("Sterile Products Bench", 2, (F.INJECTABLE,)),
    StationSpec("Topicals and Inhalers Bench", 2, (F.TOPICAL, F.INHALER)),
    StationSpec(
        "Manual Fill Bench",
        2,
        (F.TABLET, F.CAPSULE, F.LIQUID, F.INJECTABLE, F.TOPICAL, F.INHALER),
    ),
)
