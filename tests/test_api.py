"""API tests: /health, request validation, and the optimize-energy pipeline."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.replay import validate_and_totals
from app.schemas import (
    DirectiveInterpretation,
    HourlyPlan,
    OptimizeRequest,
)

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


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
            raise httpx.HTTPStatusError("error", request=request, response=self)


def _llm_response(content: str, status_code: int = 200) -> _FakeResponse:
    return _FakeResponse(
        status_code, {"choices": [{"message": {"content": content}}]}
    )


SOLAR_DIRECTIVE = {
    "note_index": 0,
    "applies": True,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
    "explanation": "Use a quarter of the solar in hours 12-13.",
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


def test_optimize_success_shape_and_totals(mocker, llm_env) -> None:
    mocker.patch(
        "app.llm.interpreter.httpx.post",
        return_value=_llm_response(json.dumps([SOLAR_DIRECTIVE])),
    )
    response = client.post("/optimize-energy", json=_valid_request())
    assert response.status_code == 200
    body = response.json()

    assert body["scenario_id"] == "TEST-01"
    assert set(body) == {
        "scenario_id",
        "directive_interpretation",
        "hourly_plan",
        "total_grid_kwh",
        "total_cost_bdt",
        "peak_grid_kwh",
        "plan_summary",
    }
    assert len(body["hourly_plan"]) == 24
    assert [entry["hour"] for entry in body["hourly_plan"]] == list(range(24))
    assert len(body["directive_interpretation"]) == len(
        _valid_request()["operator_notes"]
    )
    assert body["directive_interpretation"][0]["directive_type"] == "solar_reduction"
    assert isinstance(body["plan_summary"], str) and body["plan_summary"]

    request = OptimizeRequest.model_validate(_valid_request())
    plan = [HourlyPlan.model_validate(entry) for entry in body["hourly_plan"]]
    directives = [
        DirectiveInterpretation.model_validate(entry)
        for entry in body["directive_interpretation"]
    ]
    totals = validate_and_totals(plan, request, directives)
    assert body["total_grid_kwh"] == pytest.approx(totals.total_grid_kwh)
    assert body["total_cost_bdt"] == pytest.approx(totals.total_cost_bdt)
    assert body["peak_grid_kwh"] == pytest.approx(totals.peak_grid_kwh)


def test_optimize_malformed_json_returns_400() -> None:
    response = client.post(
        "/optimize-energy",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400


def test_optimize_non_object_body_returns_400() -> None:
    response = client.post("/optimize-energy", json=[1, 2, 3])
    assert response.status_code == 400


def test_optimize_hour_violation_returns_422(llm_env) -> None:
    payload = _valid_request()
    payload["hours"].pop()
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code in (400, 422)


def test_optimize_llm_failure_still_returns_200(mocker, llm_env) -> None:
    mocker.patch("app.llm.interpreter.time.sleep")
    mocker.patch(
        "app.llm.interpreter.httpx.post",
        return_value=_llm_response("not json at all", status_code=500),
    )
    response = client.post("/optimize-energy", json=_valid_request())
    assert response.status_code == 200
    body = response.json()
    assert len(body["directive_interpretation"]) == 1
    assert body["directive_interpretation"][0]["directive_type"] == "no_op"
    assert body["directive_interpretation"][0]["applies"] is False


def test_optimize_valid_input_never_5xx(mocker, llm_env) -> None:
    mocker.patch(
        "app.llm.interpreter.httpx.post",
        return_value=_llm_response(json.dumps([SOLAR_DIRECTIVE])),
    )
    response = client.post("/optimize-energy", json=_valid_request())
    assert response.status_code < 500
