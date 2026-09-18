"""Offline regression test across the public sample cases."""

from __future__ import annotations

from scripts.run_public_samples import COST_TOLERANCE, load_cases, run_offline


def test_offline_samples_all_pass() -> None:
    cases = load_cases()
    results = run_offline()
    assert len(results) == len(cases)
    assert len(results) >= 10
    failures = [
        (result.case_id, result.detail, result.ours_cost, result.reference_cost)
        for result in results
        if not result.passed
    ]
    assert not failures, f"public sample cases failed: {failures}"


def test_offline_samples_within_cost_tolerance() -> None:
    for result in run_offline():
        assert result.ours_cost <= result.reference_cost + COST_TOLERANCE
