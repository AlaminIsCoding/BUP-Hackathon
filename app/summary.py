"""Deterministic human-readable plan summary (no second LLM call)."""

from __future__ import annotations

from app.replay import Totals
from app.schemas import DirectiveInterpretation, DirectiveType


def _format_hours(hours: list[int] | None) -> str:
    ordered = sorted({int(hour) for hour in (hours or [])})
    if not ordered:
        return "no hours"
    runs: list[str] = []
    start = previous = ordered[0]
    for hour in ordered[1:]:
        if hour == previous + 1:
            previous = hour
            continue
        runs.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = hour
    runs.append(str(start) if start == previous else f"{start}-{previous}")
    return "hour(s) " + ", ".join(runs)


def _describe(directive: DirectiveInterpretation) -> str:
    adjustment = directive.structured_adjustment
    hours = _format_hours(adjustment.hours if adjustment else None)
    if directive.directive_type == DirectiveType.SOLAR_REDUCTION:
        factor = adjustment.factor if adjustment and adjustment.factor is not None else 0.0
        return f"solar_reduction on {hours} (usable factor {factor:g})"
    if directive.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
        minimum = (
            adjustment.minimum_energy_kwh
            if adjustment and adjustment.minimum_energy_kwh is not None
            else 0.0
        )
        return f"minimum_battery_reserve of {minimum:g} kWh on {hours}"
    if directive.directive_type == DirectiveType.NO_CHARGE_WINDOW:
        return f"no_charge_window on {hours}"
    if directive.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
        return f"no_discharge_window on {hours}"
    if directive.directive_type == DirectiveType.MAX_GRID_WINDOW:
        cap = (
            adjustment.max_grid_kwh
            if adjustment and adjustment.max_grid_kwh is not None
            else 0.0
        )
        return f"max_grid_window of {cap:g} kWh on {hours}"
    return "no_op"


def build_plan_summary(
    directives: list[DirectiveInterpretation],
    totals: Totals,
) -> str:
    """Build a deterministic summary from applied directives and recomputed totals."""

    applied = [
        directive
        for directive in directives
        if directive.applies and directive.directive_type != DirectiveType.NO_OP
    ]
    if applied:
        directive_text = "Applied " + "; ".join(_describe(d) for d in applied) + "."
    else:
        directive_text = "No operator directives affected the schedule."

    totals_text = (
        f"Total grid {totals.total_grid_kwh:.2f} kWh, "
        f"cost {totals.total_cost_bdt:.2f} BDT, "
        f"peak grid {totals.peak_grid_kwh:.2f} kWh."
    )
    return f"{directive_text} {totals_text}"
