"""MILP optimizer for the 24-hour battery/grid/solar schedule.

Builds the model described in PRD section 4.6 with PuLP/CBC and emits a rounded
hourly plan. Constraint replay lives in :mod:`app.replay`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pulp

from app.schemas import (
    BatteryAction,
    DirectiveInterpretation,
    DirectiveType,
    HourlyPlan,
    OptimizeRequest,
)

logger = logging.getLogger(__name__)

HOURS = list(range(24))
SOLVER_TIME_LIMIT_SECONDS = 10
ROUND_DECIMALS = 2


class OptimizationError(RuntimeError):
    """Raised when a feasible, optimal schedule cannot be produced."""


@dataclass(frozen=True)
class DerivedConstraints:
    """Constraints derived from validated directives, indexed by hour."""

    effective_solar: tuple[float, ...]
    min_energy: tuple[float, ...]
    no_charge: frozenset[int]
    no_discharge: frozenset[int]
    max_grid: dict[int, float]


def _round(value: float | None) -> float:
    return round(float(value or 0.0), ROUND_DECIMALS)


def derive_constraints(
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
) -> DerivedConstraints:
    """Combine directives into effective solar, reserve, window and cap limits."""

    by_hour = {entry.hour: entry for entry in request.hours}
    solar = [float(by_hour[h].solar_kwh) for h in HOURS]
    base_min = float(request.battery.minimum_energy_kwh)
    min_energy = [base_min] * 24

    factors = [1.0] * 24
    no_charge: set[int] = set()
    no_discharge: set[int] = set()
    max_grid: dict[int, float] = {}

    for directive in directives:
        adjustment = directive.structured_adjustment
        if not directive.applies or adjustment is None:
            continue
        hours = [h for h in (adjustment.hours or []) if 0 <= h < 24]

        if directive.directive_type == DirectiveType.SOLAR_REDUCTION:
            if adjustment.factor is not None:
                for hour in hours:
                    factors[hour] *= float(adjustment.factor)
        elif directive.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
            if adjustment.minimum_energy_kwh is not None:
                for hour in hours:
                    min_energy[hour] = max(
                        min_energy[hour], float(adjustment.minimum_energy_kwh)
                    )
        elif directive.directive_type == DirectiveType.NO_CHARGE_WINDOW:
            no_charge.update(hours)
        elif directive.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
            no_discharge.update(hours)
        elif directive.directive_type == DirectiveType.MAX_GRID_WINDOW:
            if adjustment.max_grid_kwh is not None:
                for hour in hours:
                    cap = float(adjustment.max_grid_kwh)
                    max_grid[hour] = min(max_grid.get(hour, cap), cap)

    effective_solar = tuple(solar[h] * factors[h] for h in HOURS)
    return DerivedConstraints(
        effective_solar=effective_solar,
        min_energy=tuple(min_energy),
        no_charge=frozenset(no_charge),
        no_discharge=frozenset(no_discharge),
        max_grid=max_grid,
    )


def optimize(
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
) -> list[HourlyPlan]:
    """Solve the MILP and return the rounded 24-hour plan.

    Raises :class:`OptimizationError` when the model is infeasible or the solver
    fails to reach optimality within the time limit.
    """

    by_hour = {entry.hour: entry for entry in request.hours}
    demand = [float(by_hour[h].demand_kwh) for h in HOURS]
    tariff = [float(by_hour[h].tariff_bdt_per_kwh) for h in HOURS]

    battery = request.battery
    capacity = float(battery.capacity_kwh)
    initial = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)
    derived = derive_constraints(request, directives)

    problem = pulp.LpProblem("gridwise_optimize", pulp.LpMinimize)
    grid = pulp.LpVariable.dicts("grid", HOURS, lowBound=0)
    solar = pulp.LpVariable.dicts("solar_used", HOURS, lowBound=0)
    charge = pulp.LpVariable.dicts("charge", HOURS, lowBound=0)
    discharge = pulp.LpVariable.dicts("discharge", HOURS, lowBound=0)
    energy = pulp.LpVariable.dicts("energy_after", HOURS, lowBound=0)
    binary = pulp.LpVariable.dicts("z", HOURS, cat=pulp.LpBinary)

    problem += pulp.lpSum(grid[h] * tariff[h] for h in HOURS)

    for h in HOURS:
        problem += grid[h] + solar[h] + discharge[h] == demand[h] + charge[h]
        problem += solar[h] <= derived.effective_solar[h]
        before = initial if h == 0 else energy[h - 1]
        problem += energy[h] == before + charge[h] - discharge[h]
        problem += energy[h] >= derived.min_energy[h]
        problem += energy[h] <= capacity
        problem += charge[h] <= max_charge
        problem += discharge[h] <= max_discharge
        problem += charge[h] <= capacity * binary[h]
        problem += discharge[h] <= capacity * (1 - binary[h])
        if h in derived.no_charge:
            problem += charge[h] == 0
        if h in derived.no_discharge:
            problem += discharge[h] == 0
        if h in derived.max_grid:
            problem += grid[h] <= derived.max_grid[h]
    problem += energy[23] == initial

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=SOLVER_TIME_LIMIT_SECONDS)
    status = problem.solve(solver)
    if pulp.LpStatus[status] != "Optimal":
        logger.warning("Optimizer failed to find an optimal plan: %s", pulp.LpStatus[status])
        raise OptimizationError(
            f"Solver did not find an optimal schedule (status={pulp.LpStatus[status]})"
        )

    charge_values = [_round(charge[h].value()) for h in HOURS]
    discharge_values = [_round(discharge[h].value()) for h in HOURS]
    solar_values = [max(0.0, _round(solar[h].value())) for h in HOURS]
    grid_values = [
        max(
            0.0,
            _round(demand[h] + charge_values[h] - discharge_values[h] - solar_values[h]),
        )
        for h in HOURS
    ]

    energy_values: list[float] = []
    previous = initial
    for h in HOURS:
        previous = round(previous + charge_values[h] - discharge_values[h], ROUND_DECIMALS)
        energy_values.append(previous)

    plan: list[HourlyPlan] = []
    for h in HOURS:
        if charge_values[h] > 0:
            action = BatteryAction.CHARGE
            magnitude = charge_values[h]
        elif discharge_values[h] > 0:
            action = BatteryAction.DISCHARGE
            magnitude = discharge_values[h]
        else:
            action = BatteryAction.IDLE
            magnitude = 0.0
        plan.append(
            HourlyPlan(
                hour=h,
                grid_kwh=grid_values[h],
                solar_used_kwh=solar_values[h],
                battery_action=action,
                battery_kwh=magnitude,
                battery_energy_after_kwh=energy_values[h],
            )
        )
    return plan
