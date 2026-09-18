# GridWise LLM-Assisted Energy Optimization API

## Why

Build the BUP CSE Fest 2026 preliminary submission: an HTTP service that turns free-text operator notes plus a 24-hour energy scenario into a validated, cost-optimal battery/grid/solar schedule. It must be reproducible and deployable for judging.

## What

A Python service exposing `GET /health` and `POST /optimize-energy` that:

1. Accepts the scenario JSON (24 hours + battery spec + 1–3 notes).
2. Uses an OpenAI-compatible LLM to map each note to one of six directives.
3. Deterministically validates/cleans LLM output (untrusted) before use.
4. Solves a MILP for the cheapest feasible 24-hour schedule.
5. Replays the schedule to verify every constraint and recomputes totals.
6. Returns the exact response schema from the Problem Statement (§10).

Done when `pytest` passes, the public sample harness reproduces reference costs within tolerance, and the Docker image reaches `/health`.

## Context

**Source of truth:** the three files in `.docs/` (Problem Statement, Participant Guide, Public Sample Cases). `PRD.md` is internal design notes only and is **not** authoritative where it disagrees with `.docs`.

**Relevant files (reference only, do not modify):**
- `.docs/BUP_CSE_FEST_2026_Preliminary_Problem_Statement_GridWise_LLM.pdf` — authoritative schemas, directives, guardrails, and optimization rules.
- `PRD.md` — non-authoritative internal design notes.
- `.docs/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` — 10 worked cases with expected directives and reference schedules; used by the regression harness. All reference `total_cost_bdt` values reconcile exactly with their `hourly_plan`, but schedules are non-unique, so compare cost within tolerance rather than byte-for-byte.
- `.docs/*.pdf` — problem statement + participant guide (informational; do not parse at runtime).

**Patterns to follow:**
- Greenfield repo: no existing code to mimic. Keep modules single-responsibility and typed.
- Pydantic v2 models as the single source of truth for request/response shapes; enums for `directive_type` and `battery_action`.

**Key decisions already made:**
- **Stack:** Python 3.11, FastAPI + Uvicorn, Pydantic v2, PuLP (CBC) for MILP, httpx for LLM calls, pytest for tests.
- **LLM:** one OpenAI-compatible Chat Completions call per request (all notes together), endpoint/base URL/model/api key from env (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). No provider-specific SDK. Tests must mock HTTP; no network in CI.
- **Optimizer:** single MILP with binary `z[h]` to forbid simultaneous charge/discharge (PRD §4.6.6). Solver time limit 10s.
- **Battery is lossless** (charge/discharge map 1:1 to energy), per the reference schedules.
- **`plan_summary` is generated deterministically** from applied directives + totals (no second LLM call), for latency and reliability.
- **Directive combination rules** (PRD is ambiguous for overlaps; lock these in):
  - `solar_reduction` factors on the same hour multiply: `effective = solar * Π factors`.
  - `minimum_battery_reserve` on the same hour takes the max minimum.
  - `max_grid_window` on the same hour takes the min cap.
  - `no_charge_window` / `no_discharge_window` union their hours.
- **Hours normalization:** if a directive's `hours` are valid integers 0–23, dedupe and sort ascending; otherwise reject the directive to `no_op`.
- **Guardrail failure fallback:** invalid/failed directive → `no_op` (`applies=false`, `structured_adjustment=null`), logged at WARNING with the reason (no secrets). Never let bad LLM output reach the optimizer.
- **Rounding:** round emitted kWh/BDT to 2 decimals, then recompute `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` from the rounded `hourly_plan` so replay matches within 0.01.

## Constraints

**Must:**
- Match PRD §4.2/§4.3 JSON shapes exactly, including `scenario_id` echo and one `directive_interpretation` entry per note in `note_index` order.
- Validate request structure → 400 for malformed/structurally invalid JSON, 422 for well-formed but semantically invalid, 500 only for controlled internal errors (generic message, no stack traces/secrets).
- Re-verify the optimizer output in the final validator before returning; reject on constraint violation.
- Read secrets only from env; never log or echo `LLM_API_KEY`.
- Bind `0.0.0.0` and honor `PORT` (default 8000).
- Keep `/health` independent of LLM/solver availability.

**Must not:**
- Add dependencies beyond: fastapi, uvicorn[standard], pydantic, httpx, pulp, pytest, pytest-mock (or stdlib `unittest.mock`).
- Modify `PRD.md` or anything under `.docs/`.
- Hard-code public case IDs, note wording, numeric values, or reference schedules anywhere in `app/`.
- Parse PDFs or make network calls during tests.

**Out of scope:**
- Auth, persistence, rate limiting, metrics, CI, the 3-minute video.
- Battery efficiency/thermal modeling, net metering/export tariffs, sub-hourly resolution.
- Multi-objective optimization (peak shaving) beyond the cost objective.

## Tasks

### T1: Project scaffold, schemas, and `/health`

**Do:** Create `app/` package. Add `app/config.py` (env settings: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `PORT`, defaults). Add `app/schemas.py` with Pydantic v2 models: `HourInput`, `BatterySpec`, `OptimizeRequest` (24 unique hours 0–23, 1–3 non-empty notes, finite non-negative numerics), `HourlyPlan`, `DirectiveInterpretation`, `OptimizeResponse`, plus `DirectiveType`/`BatteryAction` enums. Add `app/main.py` FastAPI app with `GET /health` → `{"status":"ok"}`. Add `requirements.txt`, `.env.example`, `.gitignore`, and a minimal `tests/test_api.py`.

**Files:** `app/__init__.py`, `app/config.py`, `app/schemas.py`, `app/main.py`, `requirements.txt`, `.env.example`, `.gitignore`, `tests/test_api.py`

**Verify:** `pytest tests/test_api.py` passes; `uvicorn app.main:app --port 8000` then `curl http://localhost:8000/health` returns `{"status":"ok"}`.

### T2: Deterministic guardrail validator

**Do:** Add `app/guardrails.py`: `validate_directives(raw, notes, battery) -> list[DirectiveInterpretation]`. Enforce exactly one entry per note in order; allowed `directive_type`; `no_op` ⇒ `applies=false` + `null` adjustment; non-`no_op` ⇒ `applies=true` + required fields; `hours` are unique ints 0–23 (dedupe+sort when valid); `factor ∈ [0,1]`; `0 ≤ minimum_energy_kwh ≤ capacity_kwh`; `max_grid_kwh ≥ 0`; non-empty hours. On any failure, fall back that note to `no_op` with a canned explanation and log the reason. Add `tests/test_guardrails.py` covering valid directives, each rejection path, and distractor `no_op`.

**Files:** `app/guardrails.py`, `tests/test_guardrails.py`

**Verify:** `pytest tests/test_guardrails.py` passes.

### T3: MILP optimizer and final replay validator

**Do:** Add `app/optimizer.py` building the PRD §4.6 MILP with `pulp`: vars `grid`, `solar_used`, `charge`, `discharge`, `energy_after`, binary `z`; constraints energy balance, `solar_used ≤ effective_solar` (apply combined factors), battery dynamics with `energy_before[-1]=initial`, bounds `[min_energy[h], capacity]` (raised by `minimum_battery_reserve`), rate limits, charge ≤ `M*z` / discharge ≤ `M*(1-z)` with `M = capacity_kwh`, directive windows, `energy_after[23]=initial`, non-negativity. Solve with `PULP_CBC_CMD(msg=0, timeLimit=10)`. Derive `battery_action`/`battery_kwh` per PRD §4.7; round to 2 decimals. Add `app/replay.py` `validate_and_totals(plan, request, directives)` replaying every constraint and recomputing `total_grid_kwh`/`total_cost_bdt`/`peak_grid_kwh` (tolerance 0.01). Add `tests/test_optimizer.py`: no-directive case, each directive type, overlapping directives, end-of-day neutrality, and infeasibility → controlled error.

**Files:** `app/optimizer.py`, `app/replay.py`, `tests/test_optimizer.py`

**Verify:** `pytest tests/test_optimizer.py` passes.

### T4: LLM interpreter with retry and fallback

**Do:** Add `app/llm/prompt.py` (strict system prompt requiring a JSON array matching `directive_interpretation`; few-shot examples for all six types incl. paraphrases, relative percentages such as "80% reduction" → `factor 0.2`, "half the forecast" → `factor 0.5`, windows like "6 PM until 9 PM" → `[18,19,20]`, distractors → `no_op`, and combined-notes output). Add `app/llm/interpreter.py`: single httpx POST to `{LLM_BASE_URL}/chat/completions` with `temperature=0`, JSON response when supported, timeout ~20s, one retry with exponential backoff, then per-note `no_op` fallback. Strip markdown fences before parsing; validate parsed shape defensively. Add `tests/test_llm.py` mocking httpx: happy path, malformed JSON → retry → fallback, HTTP 429 → backoff/fallback, and assert the API key never appears in logs.

**Files:** `app/llm/__init__.py`, `app/llm/prompt.py`, `app/llm/interpreter.py`, `tests/test_llm.py`

**Verify:** `pytest tests/test_llm.py` passes.

### T5: `POST /optimize-energy` end-to-end

**Do:** Add `app/summary.py` (deterministic `plan_summary` from applied directives + totals). Wire the pipeline in `app/main.py`: parse request (400 on bad JSON/structure, 422 on semantic errors) → interpret → guardrail → optimize → replay → 200 response echoing `scenario_id`. Catch solver/LLM/internal failures and return controlled 4xx/500 without stack traces. Extend `tests/test_api.py` with mocked LLM covering: success shape (all required fields, 24 hours), `scenario_id` echo, malformed JSON → 400, 24-hour violation → 400/422, LLM failure → still 200 via `no_op`, and no 5xx on valid input.

**Files:** `app/summary.py`, `app/main.py`, `tests/test_api.py`

**Verify:** `pytest tests/test_api.py` passes.

### T6: Public sample harness, Dockerfile, README

**Do:** Add `scripts/run_public_samples.py` reading `.docs/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` with two modes: offline (feed expected directives straight to optimizer+replay; assert our cost ≤ reference cost + 0.01 and totals replay clean) and live (POST `case.input` to the running API, never hard-coding answers). Add `tests/test_samples.py` running the offline mode across all 10 cases. Add `Dockerfile` (python:3.11-slim, install `coinor-cbc`, copy app, `0.0.0.0:8000`) and a README with setup, env vars, run command, `curl` examples, sample-test command, architecture, and Docker run instructions.

**Files:** `scripts/run_public_samples.py`, `tests/test_samples.py`, `Dockerfile`, `README.md`, `requirements.txt`

**Verify:** `pytest tests/test_samples.py` passes; `docker build -t gridwise .` then `docker run -p 8000:8000 --env-file .env gridwise` and `curl http://localhost:8000/health` returns `{"status":"ok"}`.

## Done

- [ ] `pytest` (full suite) passes offline with no network calls.
- [ ] `python scripts/run_public_samples.py --offline` reports all 10 cases within cost tolerance and constraint-clean.
- [ ] Manual (live): start API, POST SAMPLE-01, confirm `directive_interpretation` is `solar_reduction [12,13] factor 0.25` + `no_op`, `hourly_plan` has 24 hours, totals match replay, response < 30s.
- [ ] Docker image builds, starts with env vars, and `/health` returns 200.
- [ ] No secrets, stack traces, or hard-coded public-case answers present in repo, logs, or responses.
