"""Public sample harness for the GridWise optimizer.

Two modes:
- ``--offline`` feeds each case's expected directives straight into the optimizer
  and replay validator, asserting our cost is <= the reference cost + tolerance
  and the totals replay cleanly.
- ``--live`` POSTs each ``case.input`` to a running API (letting the service
  interpret notes itself) and validates the returned plan via replay.

No public-case answers are hard-coded or embedded in ``app/``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from app.optimizer import optimize  # noqa: E402
from app.replay import validate_and_totals  # noqa: E402
from app.schemas import (  # noqa: E402
    DirectiveInterpretation,
    DirectiveType,
    HourlyPlan,
    OptimizeRequest,
    StructuredAdjustment,
)

DEFAULT_SAMPLES_PATH = (
    REPO_ROOT / ".docs" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)
COST_TOLERANCE = 0.01
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    ours_cost: float
    reference_cost: float
    passed: bool
    detail: str


def load_cases(path: Path = DEFAULT_SAMPLES_PATH) -> list[dict[str, Any]]:
    """Load the public sample cases from disk."""

    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        for key in ("cases", "sample_cases", "samples"):
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError(f"No case list found in {path}")
    if not isinstance(data, list) or not data:
        raise ValueError(f"No cases found in {path}")
    return data


def _normalize_hours(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        found = [int(token) for token in re.findall(r"\d+", value)]
    elif isinstance(value, (list, tuple, set)):
        found = [int(token) for token in value]
    else:
        found = [int(value)]
    return sorted({hour for hour in found if 0 <= hour <= 23})


def directive_from_expected(entry: dict[str, Any]) -> DirectiveInterpretation:
    """Convert a reference directive entry into our schema type."""

    adjustment = entry.get("structured_adjustment")
    if adjustment is None:
        structured = None
    else:
        structured = StructuredAdjustment(
            hours=_normalize_hours(adjustment.get("hours")),
            factor=adjustment.get("factor"),
            minimum_energy_kwh=adjustment.get("minimum_energy_kwh"),
            max_grid_kwh=adjustment.get("max_grid_kwh"),
        )
    return DirectiveInterpretation(
        note_index=int(entry["note_index"]),
        applies=bool(entry["applies"]),
        directive_type=DirectiveType(entry["directive_type"]),
        structured_adjustment=structured,
        explanation=str(entry.get("explanation", "")),
    )


def _expected_directives(case: dict[str, Any]) -> list[DirectiveInterpretation]:
    raw = case["expected_output"]["directive_interpretation"]
    return [directive_from_expected(entry) for entry in raw]


def _reference_cost(case: dict[str, Any]) -> float:
    return float(case["expected_output"]["total_cost_bdt"])


def run_offline(path: Path = DEFAULT_SAMPLES_PATH) -> list[CaseResult]:
    """Run every case through the optimizer using its expected directives."""

    results: list[CaseResult] = []
    for case in load_cases(path):
        case_id = str(case.get("id", case["input"].get("scenario_id", "unknown")))
        request = OptimizeRequest.model_validate(case["input"])
        directives = _expected_directives(case)
        reference_cost = _reference_cost(case)
        try:
            plan = optimize(request, directives)
            totals = validate_and_totals(plan, request, directives)
        except Exception as exc:  # noqa: BLE001 - report any failure as a case result
            results.append(
                CaseResult(case_id, float("nan"), reference_cost, False, str(exc))
            )
            continue
        passed = totals.total_cost_bdt <= reference_cost + COST_TOLERANCE
        detail = "ok" if passed else "cost above reference"
        results.append(
            CaseResult(case_id, totals.total_cost_bdt, reference_cost, passed, detail)
        )
    return results


def run_live(
    base_url: str = DEFAULT_BASE_URL,
    path: Path = DEFAULT_SAMPLES_PATH,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[CaseResult]:
    """POST each case input to a running API and validate the response."""

    results: list[CaseResult] = []
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        for case in load_cases(path):
            case_id = str(case.get("id", case["input"].get("scenario_id", "unknown")))
            reference_cost = _reference_cost(case)
            try:
                response = client.post("/optimize-energy", json=case["input"])
                response.raise_for_status()
                body = response.json()
                request = OptimizeRequest.model_validate(case["input"])
                plan = [HourlyPlan.model_validate(h) for h in body["hourly_plan"]]
                directives = [
                    DirectiveInterpretation.model_validate(entry)
                    for entry in body["directive_interpretation"]
                ]
                validate_and_totals(plan, request, directives)
                cost = float(body["total_cost_bdt"])
            except Exception as exc:  # noqa: BLE001 - report any failure as a case result
                results.append(
                    CaseResult(case_id, float("nan"), reference_cost, False, str(exc))
                )
                continue
            passed = cost <= reference_cost + COST_TOLERANCE
            detail = "ok" if passed else "cost above reference"
            results.append(CaseResult(case_id, cost, reference_cost, passed, detail))
    return results


def _print_results(results: list[CaseResult]) -> int:
    for result in results:
        cost = "n/a" if result.ours_cost != result.ours_cost else f"{result.ours_cost:.2f}"
        status = "PASS" if result.passed else "FAIL"
        print(
            f"{status} {result.case_id:12} ours={cost:>10} "
            f"reference={result.reference_cost:>10.2f} ({result.detail})"
        )
    passed = sum(1 for result in results if result.passed)
    print(f"\n{passed}/{len(results)} cases within tolerance {COST_TOLERANCE}")
    return 0 if passed == len(results) else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the GridWise public sample cases.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--offline",
        action="store_true",
        help="Feed expected directives directly to the optimizer (default).",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="POST each case input to a running API.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES_PATH)
    args = parser.parse_args(argv)

    if args.live:
        results = run_live(args.base_url, args.samples, args.timeout)
    else:
        results = run_offline(args.samples)
    return _print_results(results)


if __name__ == "__main__":
    raise SystemExit(main())
