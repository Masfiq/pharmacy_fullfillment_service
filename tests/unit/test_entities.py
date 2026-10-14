import pytest

from app.domain.entities import Drug, InventoryItem, Station
from app.domain.enums import DosageForm, StationStatus, UnitType
from app.domain.errors import InsufficientStockError
from app.domain.normalize import normalize_dosage_form, normalize_strength

DRUG = Drug("0071-0155-23", "Atorvastatin Calcium", "10 mg", DosageForm.TABLET, UnitType.EACH)


def test_inventory_decrement_and_restock() -> None:
    item = InventoryItem(DRUG, quantity_on_hand=100, reorder_threshold=50)
    item.decrement(60)
    assert item.quantity_on_hand == 40
    assert item.is_low_stock
    item.restock(100)
    assert item.quantity_on_hand == 140
    assert not item.is_low_stock


def test_inventory_never_goes_negative() -> None:
    item = InventoryItem(DRUG, quantity_on_hand=10, reorder_threshold=5)
    with pytest.raises(InsufficientStockError) as exc:
        item.decrement(11)
    assert item.quantity_on_hand == 10
    assert exc.value.details == {"ndc": DRUG.ndc, "requested": 11, "on_hand": 10}


@pytest.mark.parametrize("bad", [0, -5])
def test_inventory_rejects_non_positive_quantities(bad: int) -> None:
    item = InventoryItem(DRUG, quantity_on_hand=10, reorder_threshold=5)
    with pytest.raises(ValueError):
        item.decrement(bad)
    with pytest.raises(ValueError):
        item.restock(bad)


def test_station_supports_and_status() -> None:
    station = Station(1, "Tablet Line A", 4, frozenset({DosageForm.TABLET}))
    assert station.supports(DosageForm.TABLET)
    assert not station.supports(DosageForm.LIQUID)
    assert station.is_active
    station.status = StationStatus.DOWN
    assert not station.is_active


@pytest.mark.parametrize(
    ("fda_form", "expected"),
    [
        ("TABLET, FILM COATED", DosageForm.TABLET),
        ("CAPSULE, DELAYED RELEASE", DosageForm.CAPSULE),
        ("INJECTION, SOLUTION", DosageForm.INJECTABLE),
        ("SOLUTION", DosageForm.LIQUID),
        ("POWDER, FOR SUSPENSION", DosageForm.LIQUID),
        ("AEROSOL, METERED", DosageForm.INHALER),
        ("SPRAY, METERED", DosageForm.INHALER),
        ("CREAM", DosageForm.TOPICAL),
        ("SOLUTION/ DROPS", DosageForm.LIQUID),
        ("KIT", DosageForm.OTHER),
    ],
)
def test_normalize_dosage_form(fda_form: str, expected: DosageForm) -> None:
    assert normalize_dosage_form(fda_form) is expected


def test_normalize_strength() -> None:
    assert normalize_strength(["25 mg/1", " 5 mg/1 "]) == "25 mg / 5 mg"
    assert normalize_strength(["100 [iU]/mL"]) == "100 [iU]/mL"
    assert normalize_strength(["", "10 mg/1"]) == "10 mg"


def test_dosage_form_unit_types() -> None:
    assert DosageForm.TABLET.unit_type is UnitType.EACH
    assert DosageForm.LIQUID.unit_type is UnitType.ML
    assert DosageForm.TOPICAL.unit_type is UnitType.G
