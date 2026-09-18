"""Replay and final validation of an optimized hourly plan.

Independently reconstructs battery flows, re-checks every PRD section 4.6
constraint, and recomputes totals from the rounded plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.optimizer import DerivedConstraints, derive_constraints
from app.schemas import (
    BatteryAction,
    DirectiveInterpretation,
    HourlyPlan,
    OptimizeRequest,
)

TOLERANCE = 0.01


class ConstraintViolationError(RuntimeError):
    """Raised when a replayed plan violates any schedule constraint."""


@dataclass(frozen=True)
class Totals:
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


def _flows(entry: HourlyPlan) -> tuple[float, float]:
    if entry.battery_action == BatteryAction.CHARGE:
        return float(entry.battery_kwh), 0.0
    if entry.battery_action == BatteryAction.DISCHARGE:
        return 0.0, float(entry.battery_kwh)
    return 0.0, 0.0


def _check(condition: bool, hour: int, message: str) -> None:
    if not condition:
        raise ConstraintViolationError(f"hour {hour}: {message}")


def validate_and_totals(
    plan: list[HourlyPlan],
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
) -> Totals:
    """Replay ``plan`` against every constraint and return recomputed totals."""

    if len(plan) != 24:
        raise ConstraintViolationError("hourly_plan must contain exactly 24 entries")
    by_hour = {entry.hour: entry for entry in plan}
    if sorted(by_hour) != list(range(24)):
        raise ConstraintViolationError("hourly_plan must cover each hour 0-23 exactly once")

    input_by_hour = {entry.hour: entry for entry in request.hours}
    battery = request.battery
    capacity = float(battery.capacity_kwh)
    initial = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)
    derived: DerivedConstraints = derive_constraints(request, directives)

    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    previous = initial

    for h in range(24):
        entry = by_hour[h]
        source = input_by_hour[h]
        demand = float(source.demand_kwh)
        tariff = float(source.tariff_bdt_per_kwh)
        grid_value = float(entry.grid_kwh)
        solar_value = float(entry.solar_used_kwh)
        charge_value, discharge_value = _flows(entry)
        energy_value = float(entry.battery_energy_after_kwh)

        for name, value in (
            ("grid_kwh", grid_value),
            ("solar_used_kwh", solar_value),
            ("battery_kwh", float(entry.battery_kwh)),
            ("battery_energy_after_kwh", energy_value),
        ):
            _check(value >= -TOLERANCE, h, f"{name} must be non-negative")

        _check(
            entry.battery_action == BatteryAction.IDLE or entry.battery_kwh > 0,
            h,
            "battery_action must be idle when battery_kwh is zero",
        )

        _check(
            abs((grid_value + solar_value + discharge_value) - (demand + charge_value))
            <= TOLERANCE,
            h,
            "energy balance violated",
        )
        _check(
            solar_value <= derived.effective_solar[h] + TOLERANCE,
            h,
            "solar_used_kwh exceeds effective solar",
        )
        _check(
            abs(energy_value - (previous + charge_value - discharge_value)) <= TOLERANCE,
            h,
            "battery dynamics violated",
        )
        _check(
            energy_value >= derived.min_energy[h] - TOLERANCE,
            h,
            "battery energy below required minimum",
        )
        _check(energy_value <= capacity + TOLERANCE, h, "battery energy above capacity")
        _check(charge_value <= max_charge + TOLERANCE, h, "charge rate exceeded")
        _check(discharge_value <= max_discharge + TOLERANCE, h, "discharge rate exceeded")
        _check(
            not (charge_value > TOLERANCE and discharge_value > TOLERANCE),
            h,
            "simultaneous charge and discharge",
        )
        if h in derived.no_charge:
            _check(charge_value <= TOLERANCE, h, "charging not allowed in this window")
        if h in derived.no_discharge:
            _check(discharge_value <= TOLERANCE, h, "discharging not allowed in this window")
        if h in derived.max_grid:
            _check(
                grid_value <= derived.max_grid[h] + TOLERANCE,
                h,
                "grid import exceeds cap",
            )

        total_grid += grid_value
        total_cost += grid_value * tariff
        peak_grid = max(peak_grid, grid_value)
        previous = energy_value

    _check(abs(previous - initial) <= TOLERANCE, 23, "end-of-day neutrality violated")

    return Totals(
        total_grid_kwh=round(total_grid, 2),
        total_cost_bdt=round(total_cost, 2),
        peak_grid_kwh=round(peak_grid, 2),
    )
