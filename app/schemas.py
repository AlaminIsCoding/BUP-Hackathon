"""Pydantic v2 models: the single source of truth for request/response shapes.

Mirrors the Problem Statement request schema (section 07) and response schema
(section 10).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
)


class _FiniteModel(BaseModel):
    """Base model rejecting NaN/Inf numerics."""

    model_config = ConfigDict(allow_inf_nan=False)


class DirectiveType(str, Enum):
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    IDLE = "idle"
    CHARGE = "charge"
    DISCHARGE = "discharge"


class HourInput(_FiniteModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0, allow_inf_nan=False)
    solar_kwh: float = Field(ge=0, allow_inf_nan=False)
    tariff_bdt_per_kwh: float = Field(ge=0, allow_inf_nan=False)


class BatterySpec(_FiniteModel):
    capacity_kwh: float = Field(ge=0, allow_inf_nan=False)
    initial_energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    minimum_energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    max_charge_kwh_per_hour: float = Field(ge=0, allow_inf_nan=False)
    max_discharge_kwh_per_hour: float = Field(ge=0, allow_inf_nan=False)


class OptimizeRequest(_FiniteModel):
    scenario_id: str
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourInput] = Field(min_length=24, max_length=24)
    battery: BatterySpec

    @field_validator("operator_notes")
    @classmethod
    def _notes_non_empty(cls, notes: list[str]) -> list[str]:
        if any(not note.strip() for note in notes):
            raise ValueError("operator_notes must be non-empty strings")
        return notes

    @field_validator("hours")
    @classmethod
    def _hours_cover_day(cls, hours: list[HourInput]) -> list[HourInput]:
        seen = sorted(h.hour for h in hours)
        if seen != list(range(24)):
            raise ValueError("hours must contain each hour 0-23 exactly once")
        return hours


class StructuredAdjustment(_FiniteModel):
    hours: Optional[list[int]] = None
    factor: Optional[float] = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    minimum_energy_kwh: Optional[float] = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    max_grid_kwh: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)

    @model_serializer
    def _serialize(self) -> dict:
        """Emit only the fields relevant to the directive (Section 04 shape)."""

        return {
            name: value
            for name, value in (
                ("hours", self.hours),
                ("factor", self.factor),
                ("minimum_energy_kwh", self.minimum_energy_kwh),
                ("max_grid_kwh", self.max_grid_kwh),
            )
            if value is not None
        }


class DirectiveInterpretation(_FiniteModel):
    note_index: int = Field(ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[StructuredAdjustment] = None
    explanation: str


class HourlyPlan(_FiniteModel):
    hour: int = Field(ge=0, le=23)
    grid_kwh: float = Field(ge=0, allow_inf_nan=False)
    solar_used_kwh: float = Field(ge=0, allow_inf_nan=False)
    battery_action: BatteryAction
    battery_kwh: float = Field(ge=0, allow_inf_nan=False)
    battery_energy_after_kwh: float = Field(ge=0, allow_inf_nan=False)


class OptimizeResponse(_FiniteModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlan] = Field(min_length=24, max_length=24)
    total_grid_kwh: float = Field(ge=0, allow_inf_nan=False)
    total_cost_bdt: float = Field(ge=0, allow_inf_nan=False)
    peak_grid_kwh: float = Field(ge=0, allow_inf_nan=False)
    plan_summary: str
