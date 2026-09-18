"""Tests for the deterministic guardrail validator."""

from __future__ import annotations

import logging

import pytest

from app.guardrails import FALLBACK_EXPLANATION, validate_directives
from app.schemas import BatterySpec, DirectiveType

CAPACITY = 220.0


@pytest.fixture
def battery() -> BatterySpec:
    return BatterySpec(
        capacity_kwh=CAPACITY,
        initial_energy_kwh=110.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )


def _note() -> str:
    return "note"


def _run(raw, battery: BatterySpec, count: int = 1):
    return validate_directives(raw, [_note() for _ in range(count)], battery)


@pytest.mark.parametrize(
    "candidate, expected_type, expected_adjustment",
    [
        (
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
                "explanation": "cut solar",
            },
            DirectiveType.SOLAR_REDUCTION,
            {"hours": [12, 13], "factor": 0.25},
        ),
        (
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": [18], "minimum_energy_kwh": 150},
                "explanation": "reserve",
            },
            DirectiveType.MINIMUM_BATTERY_RESERVE,
            {"hours": [18], "minimum_energy_kwh": 150.0},
        ),
        (
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": [2, 3]},
                "explanation": "no charge",
            },
            DirectiveType.NO_CHARGE_WINDOW,
            {"hours": [2, 3]},
        ),
        (
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": [19]},
                "explanation": "no discharge",
            },
            DirectiveType.NO_DISCHARGE_WINDOW,
            {"hours": [19]},
        ),
        (
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": [17, 18], "max_grid_kwh": 100},
                "explanation": "cap grid",
            },
            DirectiveType.MAX_GRID_WINDOW,
            {"hours": [17, 18], "max_grid_kwh": 100.0},
        ),
    ],
)
def test_valid_directives(
    battery: BatterySpec, candidate, expected_type, expected_adjustment
) -> None:
    result = _run([candidate], battery)
    assert len(result) == 1
    directive = result[0]
    assert directive.note_index == 0
    assert directive.applies is True
    assert directive.directive_type == expected_type
    for key, value in expected_adjustment.items():
        assert getattr(directive.structured_adjustment, key) == value
    assert directive.explanation


def test_no_op_distractor(battery: BatterySpec) -> None:
    raw = [
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "nothing to do",
        }
    ]
    directive = _run(raw, battery)[0]
    assert directive.applies is False
    assert directive.directive_type == DirectiveType.NO_OP
    assert directive.structured_adjustment is None


def test_one_entry_per_note_in_order(battery: BatterySpec) -> None:
    raw = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": 0.25},
            "explanation": "first",
        },
        {
            "note_index": 1,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "second",
        },
    ]
    result = _run(raw, battery, count=2)
    assert [d.note_index for d in result] == [0, 1]
    assert result[0].directive_type == DirectiveType.SOLAR_REDUCTION
    assert result[1].directive_type == DirectiveType.NO_OP


def test_missing_entry_falls_back(battery: BatterySpec) -> None:
    result = _run([], battery, count=2)
    assert len(result) == 2
    assert all(d.directive_type == DirectiveType.NO_OP for d in result)
    assert all(d.applies is False for d in result)


def test_non_list_payload_falls_back(battery: BatterySpec) -> None:
    result = _run({"directive_type": "no_op"}, battery, count=2)
    assert [d.directive_type for d in result] == [DirectiveType.NO_OP, DirectiveType.NO_OP]


def test_json_string_payload_is_parsed(battery: BatterySpec) -> None:
    raw = (
        '[{"note_index": 0, "applies": true, "directive_type": "no_charge_window",'
        ' "structured_adjustment": {"hours": [1]}, "explanation": "x"}]'
    )
    assert _run(raw, battery)[0].directive_type == DirectiveType.NO_CHARGE_WINDOW


def test_malformed_json_string_falls_back(battery: BatterySpec) -> None:
    result = _run("{not json", battery)
    assert result[0].directive_type == DirectiveType.NO_OP


def test_hours_are_deduped_and_sorted(battery: BatterySpec) -> None:
    raw = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [5, 1, 5, 3]},
            "explanation": "x",
        }
    ]
    assert _run(raw, battery)[0].structured_adjustment.hours == [1, 3, 5]


@pytest.mark.parametrize(
    "candidate",
    [
        "not a dict",
        {"note_index": 0, "applies": True, "directive_type": "bogus", "structured_adjustment": {}},
        {"note_index": 1, "applies": True, "directive_type": "no_op", "structured_adjustment": None},
        {"note_index": 0, "applies": True, "directive_type": "no_op", "structured_adjustment": None},
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": {"hours": [1]},
        },
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": 0.2},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [], "factor": 0.2},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [24], "factor": 0.2},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12.5], "factor": 0.2},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [True], "factor": 0.2},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": 1.5},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": "half"},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": float("nan")},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18], "minimum_energy_kwh": CAPACITY + 1},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": [17], "max_grid_kwh": -1},
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": None,
        },
    ],
)
def test_rejection_paths_fall_back_to_no_op(battery: BatterySpec, candidate) -> None:
    directive = _run([candidate], battery)[0]
    assert directive.directive_type == DirectiveType.NO_OP
    assert directive.applies is False
    assert directive.structured_adjustment is None
    assert directive.explanation == FALLBACK_EXPLANATION


def test_rejection_logs_warning_without_secrets(
    battery: BatterySpec, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "super-secret-key"
    raw = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": secret},
        }
    ]
    with caplog.at_level(logging.WARNING):
        _run(raw, battery)
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert secret not in caplog.text


def test_boundary_values_accepted(battery: BatterySpec) -> None:
    raw = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [0], "factor": 0.0},
            "explanation": "",
        },
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [23], "minimum_energy_kwh": CAPACITY},
            "explanation": "cap",
        },
    ]
    result = _run(raw, battery, count=2)
    assert result[0].structured_adjustment.factor == 0.0
    assert result[1].structured_adjustment.minimum_energy_kwh == CAPACITY
    assert result[0].explanation
