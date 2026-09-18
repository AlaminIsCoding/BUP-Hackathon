"""FastAPI application entrypoint and optimize-energy pipeline."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.guardrails import validate_directives
from app.llm.interpreter import interpret_notes
from app.optimizer import OptimizationError, optimize
from app.replay import ConstraintViolationError, Totals, validate_and_totals
from app.schemas import (
    DirectiveInterpretation,
    HourlyPlan,
    OptimizeRequest,
    OptimizeResponse,
)
from app.summary import build_plan_summary

logger = logging.getLogger(__name__)

app = FastAPI(title="GridWise Energy Optimization API", version="0.1.0")


def _error(status_code: int, detail: str, errors: Any = None) -> JSONResponse:
    content: dict[str, Any] = {"detail": detail}
    if errors is not None:
        content["errors"] = errors
    return JSONResponse(status_code=status_code, content=content)


def _safe_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe, independent of LLM/solver availability."""

    return {"status": "ok"}


def _run_pipeline(
    parsed: OptimizeRequest,
) -> tuple[list[DirectiveInterpretation], list[HourlyPlan], Totals]:
    """Run the blocking LLM + solver pipeline off the event loop.

    The interpreter performs synchronous HTTP and the MILP solver is CPU-bound,
    so both must run in a worker thread to keep the API responsive under
    concurrent judge requests.
    """

    raw_directives = interpret_notes(parsed.operator_notes, parsed.battery)
    directives = validate_directives(
        raw_directives, parsed.operator_notes, parsed.battery
    )
    plan = optimize(parsed, directives)
    totals = validate_and_totals(plan, parsed, directives)
    return directives, plan, totals


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(request: Request):
    """Interpret notes, optimize the schedule, and return the validated plan."""

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error(400, "Request body must be valid JSON")

    if not isinstance(payload, dict):
        return _error(400, "Request body must be a JSON object")

    try:
        parsed = OptimizeRequest.model_validate(payload)
    except ValidationError as exc:
        # Problem Statement 6.1: 400 for malformed or structurally invalid
        # requests; 422 is optional, so 400 is used for all validation failures.
        return _error(400, "Request failed validation", _safe_errors(exc))

    try:
        directives, plan, totals = await run_in_threadpool(_run_pipeline, parsed)
    except OptimizationError:
        logger.warning("Optimizer could not find a feasible schedule")
        return _error(422, "No feasible schedule exists for the given scenario")
    except ConstraintViolationError:
        logger.error("Final validation rejected the optimized schedule")
        return _error(500, "Internal server error")
    except Exception:
        logger.error("Unexpected failure while optimizing the schedule")
        return _error(500, "Internal server error")

    return OptimizeResponse(
        scenario_id=parsed.scenario_id,
        directive_interpretation=directives,
        hourly_plan=plan,
        total_grid_kwh=totals.total_grid_kwh,
        total_cost_bdt=totals.total_cost_bdt,
        peak_grid_kwh=totals.peak_grid_kwh,
        plan_summary=build_plan_summary(directives, totals),
    )
