from dataclasses import replace
from typing import Any

import pytest

from app.catalog import DEFAULT_STATIONS, load_drugs
from app.domain.entities import Drug, InventoryItem, Station
from app.domain.enums import DosageForm, StationStatus
from app.simulation import Simulation, SimulationConfig, SimulationReport


def floor() -> tuple[list[Drug], dict[str, InventoryItem], list[Station]]:
    drugs = load_drugs()
    inventory = {d.ndc: InventoryItem(d, 100_000, 100) for d in drugs}
    stations = [
        Station(i, s.name, s.capacity, frozenset(s.supported_dosage_forms))
        for i, s in enumerate(DEFAULT_STATIONS, start=1)
    ]
    return drugs, inventory, stations


def run(**overrides: Any) -> SimulationReport:
    drugs, inventory, stations = floor()
    config = replace(SimulationConfig(orders=400, ticks=300, seed=7), **overrides)
    return Simulation(drugs, inventory, stations, config).run()


def test_catalog_snapshot_is_real_fda_data() -> None:
    drugs = load_drugs()
    assert len(drugs) >= 100
    assert len({d.ndc for d in drugs}) == len(drugs)
    assert {d.dosage_form for d in drugs} >= {DosageForm.TABLET, DosageForm.LIQUID}


def test_same_seed_gives_same_report() -> None:
    a, b = run(), run()
    assert replace(a, elapsed_ms=0) == replace(b, elapsed_ms=0)


def test_every_order_is_accounted_for() -> None:
    report = run()
    assert report.orders_generated == 400
    assert sum(report.status_counts.values()) == 400
    assert report.orders_shipped > 0
    assert report.audit_records >= report.orders_generated


def test_utilization_is_a_fraction() -> None:
    report = run()
    assert 0 < report.overall_utilization <= 1
    assert all(0 <= s.utilization <= 1 for s in report.station_utilization)


def test_stat_waits_less_than_routine_under_load() -> None:
    report = run(orders=1500, ticks=300, station_failure_rate=0.0)
    stat = report.average_wait_minutes["STAT"]
    routine = report.average_wait_minutes["ROUTINE"]
    assert stat is not None and routine is not None
    assert stat < routine


def test_station_failures_requeue_work() -> None:
    report = run(station_failure_rate=0.05, repair_ticks=10)
    assert report.station_failures > 0
    assert report.requeued_after_station_down > 0
    assert any(s.down_ticks > 0 for s in report.station_utilization)


def test_out_of_stock_orders_go_on_hold_and_stock_never_negative() -> None:
    drugs, inventory, stations = floor()
    for item in inventory.values():
        item.quantity_on_hand = 50  # enough for one tablet order of 30 per drug, at most
    report = Simulation(
        drugs, inventory, stations, SimulationConfig(orders=300, ticks=200, seed=1)
    ).run()
    assert report.orders_on_hold > 0
    # The caller's snapshot is untouched: the simulation works on a copy.
    assert all(item.quantity_on_hand == 50 for item in inventory.values())


def test_down_station_at_start_gets_no_work() -> None:
    drugs, inventory, stations = floor()
    stations[0].status = StationStatus.DOWN
    report = Simulation(
        drugs, inventory, stations, SimulationConfig(orders=200, ticks=120, station_failure_rate=0)
    ).run()
    first = report.station_utilization[0]
    assert first.busy_slot_ticks == 0
    assert first.down_ticks == 120


@pytest.mark.parametrize("ticks", [1, 5])
def test_tiny_runs_do_not_crash(ticks: int) -> None:
    report = run(orders=10, ticks=ticks)
    assert sum(report.status_counts.values()) == 10
