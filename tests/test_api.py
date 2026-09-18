"""Minimal API scaffold tests: /health plus request-schema validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas import OptimizeRequest

client = TestClient(app)


def _valid_request() -> dict:
    return {
        "scenario_id": "TEST-01",
        "operator_notes": ["No adjustments today."],
        "hours": [
            {
                "hour": h,
                "demand_kwh": 90.0,
                "solar_kwh": 0.0,
                "tariff_bdt_per_kwh": 6.0,
            }
            for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 220.0,
            "initial_energy_kwh": 110.0,
            "minimum_energy_kwh": 40.0,
            "max_charge_kwh_per_hour": 50.0,
            "max_discharge_kwh_per_hour": 50.0,
        },
    }


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_valid_request_parses() -> None:
    request = OptimizeRequest.model_validate(_valid_request())
    assert request.scenario_id == "TEST-01"
    assert len(request.hours) == 24


def test_requires_24_unique_hours() -> None:
    payload = _valid_request()
    payload["hours"].pop()
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(payload)


def test_rejects_empty_note_and_bad_numeric() -> None:
    payload = _valid_request()
    payload["operator_notes"] = ["   "]
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(payload)

    payload = _valid_request()
    payload["hours"][0]["demand_kwh"] = -1
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(payload)
