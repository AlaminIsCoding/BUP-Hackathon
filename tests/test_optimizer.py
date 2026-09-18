"""Tests for the MILP optimizer and replay validator."""

from __future__ import annotations

import math

import pytest

from app.optimizer import OptimizationError, derive_constraints, optimize
from app.replay import ConstraintViolationError, Totals, validate_and_totals
from app.schemas import (
    BatteryAction,
    DirectiveInterpretation,
    DirectiveType,
    HourlyPlan,
    OptimizeRequest,
    StructuredAdjustment,
)

BASE_BATTERY = {
    "capacity_kwh": 200.0,
    "initial_energy_kwh": 100.0,
    "minimum_energy_kwh": 20.0,
    "max_charge_kwh_per_hour": 50.0,
    "max_discharge_kwh_per_hour": 50.0,
}

BASE_SOLAR = {10: 100.0, 11: 100.0, 12: 100.0, 13: 100.0, 14: 100.0}


def _tariff(hour: int) -> float:
    if hour < 7:
        return 5.0
    if hour >= 17:
        return 20.0
    return 8.0


def make_request(
    battery: dict | None = None,
    solar: dict | None = None,
    demand: float = 100.0,
) -> OptimizeRequest:
    solar = BASE_SOLAR if solar is None else solar
    payload = {
        "scenario_id": "TEST-CASE",
        "operator_notes": ["no-op note"],
        "hours": [
            {
                "hour": h,
                "demand_kwh": demand,
                "solar_kwh": solar.get(h, 0.0),
                "tariff_bdt_per_kwh": _tariff(h),
            }
            for h in range(24)
        ],
        "battery": battery or BASE_BATTERY,
    }
    return OptimizeRequest.model_validate(payload)


def _directive(
    directive_type: DirectiveType,
    hours: list[int],
    **adjustments: float,
) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type=directive_type,
        structured_adjustment=StructuredAdjustment(hours=hours, **adjustments),
        explanation="test directive",
    )


def _run(request: OptimizeRequest, directives: list[DirectiveInterpretation]):
    plan = optimize(request, directives)
    totals = validate_and_totals(plan, request, directives)
    return plan, totals


def test_no_directive_plan_is_valid_and_neutral() -> None:
    request = make_request()
    plan, totals = _run(request, [])
    assert len(plan) == 24
    assert [entry.hour for entry in plan] == list(range(24))
    assert isinstance(totals, Totals)
    assert plan[23].battery_energy_after_kwh == pytest.approx(
        request.battery.initial_energy_kwh, abs=0.01
    )
    recomputed_grid = round(sum(entry.grid_kwh for entry in plan), 2)
    assert totals.total_grid_kwh == pytest.approx(recomputed_grid, abs=0.01)


def test_arbitrary_precision_scenario_replays_cleanly() -> None:
    """Rounding must not drift the replayed battery state beyond tolerance."""

    request = OptimizeRequest.model_validate(
        {
            "scenario_id": "PRECISION",
            "operator_notes": ["no directive"],
            "hours": [
                {
                    "hour": h,
                    "demand_kwh": 100.0 + 0.1234567 * h,
                    "solar_kwh": max(0.0, 50.0 * math.sin(h / 3.0)),
                    "tariff_bdt_per_kwh": 5.0 + 0.9876543 * (h % 7),
                }
                for h in range(24)
            ],
            "battery": {
                "capacity_kwh": 173.3333333,
                "initial_energy_kwh": 88.1234567,
                "minimum_energy_kwh": 12.9876543,
                "max_charge_kwh_per_hour": 43.3333333,
                "max_discharge_kwh_per_hour": 47.7777777,
            },
        }
    )
    plan, _ = _run(request, [])
    assert plan[23].battery_energy_after_kwh == pytest.approx(
        request.battery.initial_energy_kwh, abs=0.01
    )


def test_replay_accepts_small_non_idle_flows() -> None:
    request = make_request()
    plan = [
        HourlyPlan(
            hour=hour,
            grid_kwh=100.0,
            solar_used_kwh=0.0,
            battery_action=BatteryAction.IDLE,
            battery_kwh=0.0,
            battery_energy_after_kwh=100.0,
        )
        for hour in range(24)
    ]
    plan[0] = HourlyPlan(
        hour=0,
        grid_kwh=99.995,
        solar_used_kwh=0.0,
        battery_action=BatteryAction.DISCHARGE,
        battery_kwh=0.005,
        battery_energy_after_kwh=99.995,
    )
    plan[1] = HourlyPlan(
        hour=1,
        grid_kwh=100.005,
        solar_used_kwh=0.0,
        battery_action=BatteryAction.CHARGE,
        battery_kwh=0.005,
        battery_energy_after_kwh=100.0,
    )

    validate_and_totals(plan, request, [])


def test_solar_reduction_caps_usable_solar() -> None:
    request = make_request()
    directive = _directive(DirectiveType.SOLAR_REDUCTION, [10, 11], factor=0.2)
    plan, _ = _run(request, [directive])
    assert plan[10].solar_used_kwh <= 20.01
    assert plan[11].solar_used_kwh <= 20.01


def test_minimum_battery_reserve_is_honored() -> None:
    request = make_request()
    directive = _directive(
        DirectiveType.MINIMUM_BATTERY_RESERVE, [18, 19, 20], minimum_energy_kwh=150.0
    )
    plan, _ = _run(request, [directive])
    for hour in (18, 19, 20):
        assert plan[hour].battery_energy_after_kwh >= 149.99


def test_no_charge_window_blocks_charging() -> None:
    request = make_request()
    directive = _directive(DirectiveType.NO_CHARGE_WINDOW, [3, 4])
    plan, _ = _run(request, [directive])
    for hour in (3, 4):
        assert plan[hour].battery_action != BatteryAction.CHARGE


def test_no_discharge_window_blocks_discharging() -> None:
    request = make_request()
    directive = _directive(DirectiveType.NO_DISCHARGE_WINDOW, [19, 20])
    plan, _ = _run(request, [directive])
    for hour in (19, 20):
        assert plan[hour].battery_action != BatteryAction.DISCHARGE


def test_max_grid_window_caps_import() -> None:
    request = make_request()
    directive = _directive(DirectiveType.MAX_GRID_WINDOW, [17, 18], max_grid_kwh=60.0)
    plan, _ = _run(request, [directive])
    for hour in (17, 18):
        assert plan[hour].grid_kwh <= 60.01


def test_overlapping_directives_combine() -> None:
    request = make_request()
    directives = [
        _directive(DirectiveType.SOLAR_REDUCTION, [10, 11], factor=0.5),
        _directive(DirectiveType.SOLAR_REDUCTION, [10, 12], factor=0.5),
        _directive(
            DirectiveType.MINIMUM_BATTERY_RESERVE, [18], minimum_energy_kwh=80.0
        ),
        _directive(
            DirectiveType.MINIMUM_BATTERY_RESERVE, [18], minimum_energy_kwh=150.0
        ),
        _directive(DirectiveType.MAX_GRID_WINDOW, [17], max_grid_kwh=90.0),
        _directive(DirectiveType.MAX_GRID_WINDOW, [17], max_grid_kwh=70.0),
        _directive(DirectiveType.NO_CHARGE_WINDOW, [1, 2]),
        _directive(DirectiveType.NO_CHARGE_WINDOW, [2, 3]),
    ]
    derived = derive_constraints(request, directives)
    assert derived.effective_solar[10] == pytest.approx(25.0)
    assert derived.effective_solar[11] == pytest.approx(50.0)
    assert derived.effective_solar[12] == pytest.approx(50.0)
    assert derived.min_energy[18] == pytest.approx(150.0)
    assert derived.max_grid[17] == pytest.approx(70.0)
    assert derived.no_charge == frozenset({1, 2, 3})


def test_infeasible_model_raises_controlled_error() -> None:
    request = make_request(
        battery={
            "capacity_kwh": 0.0,
            "initial_energy_kwh": 0.0,
            "minimum_energy_kwh": 0.0,
            "max_charge_kwh_per_hour": 0.0,
            "max_discharge_kwh_per_hour": 0.0,
        }
    )
    directive = _directive(DirectiveType.MAX_GRID_WINDOW, [0], max_grid_kwh=50.0)
    with pytest.raises(OptimizationError):
        optimize(request, [directive])


def test_replay_rejects_tampered_plan() -> None:
    request = make_request()
    plan = optimize(request, [])
    tampered = list(plan)
    tampered[0] = plan[0].model_copy(update={"grid_kwh": plan[0].grid_kwh + 500.0})
    with pytest.raises(ConstraintViolationError):
        validate_and_totals(tampered, request, [])


def test_replay_rejects_wrong_hour_count() -> None:
    request = make_request()
    plan = optimize(request, [])
    with pytest.raises(ConstraintViolationError):
        validate_and_totals(plan[:-1], request, [])
