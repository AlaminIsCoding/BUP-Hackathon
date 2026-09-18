"""Deterministic guardrail validator for untrusted LLM directive output.

LLM output is never trusted: every directive is re-validated here before it can
reach the optimizer. Any failure falls that note back to a safe ``no_op`` and is
logged at WARNING with the reason (never secrets).
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from app.schemas import (
    BatterySpec,
    DirectiveInterpretation,
    DirectiveType,
    StructuredAdjustment,
)

logger = logging.getLogger(__name__)

FALLBACK_EXPLANATION = "Directive could not be validated and was treated as no_op."

_ALLOWED_TYPES = {directive.value for directive in DirectiveType}


def _is_number(value: Any) -> bool:
    """True for finite real numbers (excludes bool and NaN/Inf)."""

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _coerce_number(value: Any) -> Optional[float]:
    """Return a finite float for numbers or numeric strings, else None."""

    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if math.isfinite(parsed):
            return parsed
    return None


def _coerce_hour(item: Any) -> Optional[int]:
    if isinstance(item, bool):
        return None
    if isinstance(item, int):
        return item
    if isinstance(item, str):
        text = item.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _parse_hours(value: Any) -> Optional[list[int]]:
    """Normalize hours to a sorted, de-duplicated list, or None if invalid."""

    if not isinstance(value, list) or not value:
        return None
    hours: list[int] = []
    for item in value:
        hour = _coerce_hour(item)
        if hour is None or not 0 <= hour <= 23:
            return None
        hours.append(hour)
    return sorted(set(hours))


def _make_no_op(note_index: int, explanation: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type=DirectiveType.NO_OP,
        structured_adjustment=None,
        explanation=explanation,
    )


def _clean_explanation(candidate: Any, directive_type: str) -> str:
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return f"Applied {directive_type} directive."


def _validate_candidate(
    candidate: Any,
    note_index: int,
    capacity_kwh: float,
) -> DirectiveInterpretation:
    """Validate one raw entry, raising ValueError with the rejection reason."""

    if not isinstance(candidate, dict):
        raise ValueError("entry is not an object")

    raw_index = candidate.get("note_index")
    if raw_index is not None and (
        isinstance(raw_index, bool)
        or not isinstance(raw_index, int)
        or raw_index != note_index
    ):
        raise ValueError(f"note_index {raw_index!r} is not in order")

    directive_type = candidate.get("directive_type")
    if not isinstance(directive_type, str) or directive_type not in _ALLOWED_TYPES:
        raise ValueError(f"unsupported directive_type {directive_type!r}")

    applies = candidate.get("applies")
    adjustment = candidate.get("structured_adjustment")

    if directive_type == DirectiveType.NO_OP.value:
        if applies is not False:
            raise ValueError("no_op requires applies=false")
        if adjustment is not None:
            raise ValueError("no_op requires structured_adjustment=null")
        return _make_no_op(
            note_index, _clean_explanation(candidate.get("explanation"), "no_op")
        )

    if applies is not True:
        raise ValueError(f"{directive_type} requires applies=true")
    if not isinstance(adjustment, dict):
        raise ValueError(f"{directive_type} requires a structured_adjustment object")

    hours = _parse_hours(adjustment.get("hours"))
    if hours is None:
        raise ValueError("hours must be a non-empty list of unique integers 0-23")

    explanation = _clean_explanation(candidate.get("explanation"), directive_type)

    if directive_type == DirectiveType.SOLAR_REDUCTION.value:
        factor = _coerce_number(adjustment.get("factor"))
        if factor is None or not 0 <= factor <= 1:
            raise ValueError("factor must be a number in [0, 1]")
        structured = StructuredAdjustment(hours=hours, factor=factor)
    elif directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE.value:
        minimum = _coerce_number(adjustment.get("minimum_energy_kwh"))
        if minimum is None or not 0 <= minimum <= capacity_kwh:
            raise ValueError("minimum_energy_kwh must be in [0, capacity_kwh]")
        structured = StructuredAdjustment(hours=hours, minimum_energy_kwh=minimum)
    elif directive_type == DirectiveType.MAX_GRID_WINDOW.value:
        max_grid = _coerce_number(adjustment.get("max_grid_kwh"))
        if max_grid is None or max_grid < 0:
            raise ValueError("max_grid_kwh must be a non-negative number")
        structured = StructuredAdjustment(hours=hours, max_grid_kwh=max_grid)
    else:
        structured = StructuredAdjustment(hours=hours)

    return DirectiveInterpretation(
        note_index=note_index,
        applies=True,
        directive_type=DirectiveType(directive_type),
        structured_adjustment=structured,
        explanation=explanation,
    )


def validate_directives(
    raw: Any,
    notes: list[str],
    battery: BatterySpec,
) -> list[DirectiveInterpretation]:
    """Return exactly one validated directive per note, in note order.

    ``raw`` is untrusted and may be a JSON string, a list of dicts, or anything
    else. Invalid or missing entries become ``no_op`` with a canned explanation.
    """

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Directive payload is not valid JSON; using no_op for all notes")
            raw = None

    if isinstance(raw, list):
        entries: list[Any] = raw
    else:
        logger.warning("Directive payload is not a list; using no_op for all notes")
        entries = []

    if len(entries) != len(notes):
        logger.warning(
            "Expected %d directive(s) but received %d", len(notes), len(entries)
        )

    capacity_kwh = float(battery.capacity_kwh)
    validated: list[DirectiveInterpretation] = []
    for index, _note in enumerate(notes):
        candidate = entries[index] if index < len(entries) else None
        try:
            validated.append(_validate_candidate(candidate, index, capacity_kwh))
        except ValueError as exc:
            logger.warning("Directive for note %d rejected: %s", index, exc)
            validated.append(_make_no_op(index, FALLBACK_EXPLANATION))
    return validated
