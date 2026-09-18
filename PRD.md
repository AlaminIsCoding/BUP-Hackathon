# PRD: GridWise LLM-Assisted Energy Optimization API

**Document Version:** 1.0  
**Date:** 2026-09-18  
**Status:** Draft  
**Author:** Engineering Team  
**Event:** BUP CSE Fest 2026 · Hackathon · Online Preliminary

---

## 1. Overview

GridWise is a smart campus energy optimization challenge. The goal is to build an HTTP API that:

1. Accepts a 24-hour energy scenario (demand, solar, tariff, battery specs).
2. Accepts 1–3 natural-language operator notes.
3. Uses an LLM to interpret each note into a strict, machine-readable directive.
4. Validates the LLM output deterministically.
5. Applies valid directives to an optimization model.
6. Returns the cheapest valid 24-hour schedule (grid usage, solar usage, battery actions) as JSON.

The system must be deployed as a public HTTP API, reproducible locally, and documented thoroughly. A 3-minute architecture video is required for tie-breaking.

---

## 2. Goals and Objectives

| Goal                       | Description                                                                                                         |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Correct Interpretation** | Convert free-text operator notes into one of six supported directive types with exact hours and numeric values.     |
| **Robust Guardrails**      | Deterministically validate LLM output before it reaches the optimizer.                                              |
| **Optimal Scheduling**     | Minimize total grid electricity cost over 24 hours while satisfying all energy, battery, and directive constraints. |
| **Reliable API**           | Expose `GET /health` and `POST /optimize-energy` with exact request/response schemas.                               |
| **Reproducibility**        | Provide a self-contained README, Docker fallback image, and local test procedure.                                   |
| **High Performance**       | Respond within 30 seconds (target p95 < 5s).                                                                        |
| **Security**               | No secrets in repo, logs, or responses.                                                                             |

---

## 3. System Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Energy Data +  │────▶│  LLM            │────▶│  Guardrail      │
│  Operator Notes │     │  Interpreter    │     │  Validator      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                         │
                                                         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  API Response   │◀────│  Final          │◀────│  Math           │
│                 │     │  Validator      │     │  Optimizer      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

**Components:**

- **API Layer:** FastAPI / Flask / Express / etc. Handles HTTP, JSON validation, error handling.
- **LLM Interpreter:** Uses a language model (e.g., GPT-4, Claude, Llama) to parse notes into structured directives.
- **Guardrail Validator:** Deterministic Python code that checks directive types, hours, factors, numeric ranges, and `applies` semantics.
- **Optimizer:** Linear programming (LP) or MILP solver (e.g., PuLP, OR-Tools, SciPy) to compute optimal schedule.
- **Final Validator:** Replays schedule to ensure all constraints are met and recalculates totals.

---

## 4. Functional Requirements

### 4.1 API Endpoints

| Endpoint           | Method | Description                                                |
| ------------------ | ------ | ---------------------------------------------------------- |
| `/health`          | GET    | Readiness check. Returns `{"status": "ok"}` with HTTP 200. |
| `/optimize-energy` | POST   | Accepts scenario JSON, returns interpretation + schedule.  |

**Response Codes:**

- `200` – Success
- `400` – Malformed JSON or structurally invalid request
- `422` – Semantically invalid but well-formed request (optional)
- `500` – Controlled internal error (no stack traces/secrets)

### 4.2 Request Schema

```json
{
  "scenario_id": "string",
  "operator_notes": ["string", "..."], // 1-3 non-empty strings
  "hours": [
    {
      "hour": 0,
      "demand_kwh": 90,
      "solar_kwh": 0,
      "tariff_bdt_per_kwh": 6
    }
    // ... exactly 24 entries, hours 0-23
  ],
  "battery": {
    "capacity_kwh": 220,
    "initial_energy_kwh": 110,
    "minimum_energy_kwh": 40,
    "max_charge_kwh_per_hour": 50,
    "max_discharge_kwh_per_hour": 50
  }
}
```

**Validation:**

- `hours` must have exactly 24 unique entries for hours 0–23.
- `operator_notes` must have 1–3 non-empty strings.
- All numeric fields must be finite and non-negative.

### 4.3 Response Schema

```json
{
  "scenario_id": "string",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [12, 13],
        "factor": 0.25
      },
      "explanation": "string"
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 90,
      "solar_used_kwh": 0,
      "battery_action": "idle",
      "battery_kwh": 0,
      "battery_energy_after_kwh": 110
    }
    // ... exactly 24 entries
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365,
  "peak_grid_kwh": 175,
  "plan_summary": "string"
}
```

**Validation:**

- One `directive_interpretation` per note, in `note_index` order.
- `hourly_plan` must have 24 unique hours 0–23.
- `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` must match recalculated values from `hourly_plan`.
- All numeric values finite and non-negative.

### 4.4 Operator Note Interpretation

The LLM must convert each note into exactly one directive type. Supported types:

| Directive                 | Meaning                               | Structured Adjustment                           |
| ------------------------- | ------------------------------------- | ----------------------------------------------- |
| `solar_reduction`         | Reduce usable solar in specific hours | `{"hours": [int], "factor": float}`             |
| `minimum_battery_reserve` | Battery energy ≥ required level       | `{"hours": [int], "minimum_energy_kwh": float}` |
| `no_charge_window`        | Charging unavailable                  | `{"hours": [int]}`                              |
| `no_discharge_window`     | Discharging unavailable               | `{"hours": [int]}`                              |
| `max_grid_window`         | Grid import cap                       | `{"hours": [int], "max_grid_kwh": float}`       |
| `no_op`                   | Note does not affect schedule         | `null`                                          |

**Rules:**

- `no_op`: `applies = false`, `structured_adjustment = null`.
- All other directives: `applies = true`.
- `hours` must be unique integers 0–23 in ascending order.
- Time windows: start-inclusive, end-exclusive. “1 PM to 3 PM” → `[13, 14]`.
- For `solar_reduction`, `factor` is the usable fraction remaining. “80% reduction” → `0.2`. “25% of forecast” → `0.25`.

### 4.5 Guardrails and Validation

LLM output is untrusted. After LLM generates directives, deterministic code must:

1. Ensure exactly one entry per note, in order.
2. Check `directive_type` is one of the six allowed.
3. For `no_op`: verify `applies = false` and `structured_adjustment = null`.
4. For other types: verify `applies = true` and required fields exist.
5. Validate `hours`: unique integers 0–23, ascending.
6. Validate numeric values: `factor` between 0 and 1, `minimum_energy_kwh` ≤ capacity, `max_grid_kwh` ≥ 0.
7. If any check fails, reject the interpretation or fall back to a safe default (e.g., `no_op` for that note) and log the error.

### 4.6 Optimization Model

**Objective:** Minimize total grid cost:

```
total_cost_bdt = Σ (grid_kwh[h] * tariff_bdt_per_kwh[h]) for h = 0..23
```

**Decision Variables (for each hour h):**

- `grid_kwh[h] ≥ 0`
- `solar_used_kwh[h] ≥ 0`
- `battery_charge_kwh[h] ≥ 0`
- `battery_discharge_kwh[h] ≥ 0`
- `battery_energy_after_kwh[h]`

**Constraints:**

1. **Energy Balance:**

   ```
   grid_kwh[h] + solar_used_kwh[h] + battery_discharge_kwh[h]
   = demand_kwh[h] + battery_charge_kwh[h]
   ```

2. **Solar Usage:**

   ```
   0 ≤ solar_used_kwh[h] ≤ effective_solar_kwh[h]
   ```

   where `effective_solar_kwh[h] = solar_kwh[h] * factor` if `solar_reduction` applies, else `solar_kwh[h]`.

3. **Battery Energy Dynamics:**

   ```
   battery_energy_after_kwh[h] = battery_energy_after_kwh[h-1] + battery_charge_kwh[h] - battery_discharge_kwh[h]
   ```

   with `battery_energy_after_kwh[-1] = initial_energy_kwh`.

4. **Battery Bounds:**

   ```
   min_energy[h] ≤ battery_energy_after_kwh[h] ≤ capacity_kwh
   ```

   where `min_energy[h] = max(minimum_energy_kwh, directive_minimum)` if `minimum_battery_reserve` applies.

5. **Charge/Discharge Rate Limits:**

   ```
   battery_charge_kwh[h] ≤ max_charge_kwh_per_hour
   battery_discharge_kwh[h] ≤ max_discharge_kwh_per_hour
   ```

6. **No Simultaneous Charge/Discharge:**
   Use binary variables or rely on cost minimization. To be safe:

   ```
   battery_charge_kwh[h] ≤ M * z[h]
   battery_discharge_kwh[h] ≤ M * (1 - z[h])
   ```

   where `z[h] ∈ {0,1}`.

7. **Directive Constraints:**
   - `no_charge_window`: `battery_charge_kwh[h] = 0` for listed hours.
   - `no_discharge_window`: `battery_discharge_kwh[h] = 0` for listed hours.
   - `max_grid_window`: `grid_kwh[h] ≤ max_grid_kwh` for listed hours.

8. **End-of-Day Neutrality:**

   ```
   battery_energy_after_kwh[23] = initial_energy_kwh
   ```

9. **Non-Negativity:** All variables ≥ 0.

### 4.7 Battery Action Output

From optimized variables, derive:

- `battery_kwh = battery_charge_kwh[h] - battery_discharge_kwh[h]`? No, output magnitude and action.
- If `battery_charge_kwh[h] > 0`: action = `charge`, `battery_kwh = battery_charge_kwh[h]`.
- Else if `battery_discharge_kwh[h] > 0`: action = `discharge`, `battery_kwh = battery_discharge_kwh[h]`.
- Else: action = `idle`, `battery_kwh = 0`.

`battery_energy_after_kwh[h]` is the state after the hour.

---

## 5. Non-Functional Requirements

### 5.1 Performance

- `POST /optimize-energy` must complete within **30 seconds**.
- Target **p95 latency < 5 seconds**.
- Health check must respond within **60 seconds** of service start.

### 5.2 Reliability

- Valid requests must not return 5xx errors.
- Malformed input must return controlled 400/422 errors, not crash.
- LLM/provider failures must be handled gracefully (e.g., fallback to `no_op` or retry).

### 5.3 Security

- No API keys, tokens, or secrets in repository, logs, or responses.
- Use environment variables for secrets.
- Do not expose stack traces.

### 5.4 Reproducibility

- README must allow a clean environment to:
  - Clone repo
  - Set env vars
  - Install dependencies
  - Start service
  - Call `/health`
  - Run at least one public sample
- Docker fallback image must be pullable and reach `/health` with documented command.

---

## 6. Implementation Details

### 6.1 LLM Integration

- **Model:** Choose a reliable LLM (e.g., OpenAI GPT-4, Anthropic Claude, local Llama). Document model/provider in README.
- **Prompt Engineering:** Design a system prompt that instructs the LLM to output strict JSON matching the `directive_interpretation` schema. Include examples of each directive type and paraphrases.
- **Output Parsing:** Use a JSON parser with fallback. If parsing fails, retry once, then fallback to `no_op` for that note.
- **Rate Limits:** Handle provider rate limits with exponential backoff.

### 6.2 Deterministic Guardrails

Implement a Python module (or equivalent) that:

- Takes LLM JSON output.
- Validates against the schema and rules in Section 4.5.
- Returns a cleaned list of directives or raises an exception.
- Logs invalid attempts for debugging (without secrets).

### 6.3 Optimizer Implementation

- **Solver:** Use PuLP with CBC, OR-Tools, or SciPy `linprog` for LP. For binary variables (simultaneous charge/discharge), use MILP solver (e.g., CBC, GLPK).
- **Model Construction:** Build the LP/MILP as described in Section 4.6.
- **Solver Time Limit:** Set a time limit (e.g., 10 seconds) to ensure 30s API timeout is not exceeded.
- **Rounding:** Round output to 2 decimal places (or as needed) but ensure totals match within tolerance.

### 6.4 Battery and Solar Modeling

- **Effective Solar:** Precompute `effective_solar_kwh[h]` after applying `solar_reduction` directives.
- **Minimum Reserve:** For hours with `minimum_battery_reserve`, set `min_energy[h] = max(base_min, directive_min)`.
- **No-Charge/No-Discharge:** Add constraints to zero out respective variables.
- **Max Grid:** Add upper bound on `grid_kwh[h]`.

---

## 7. Deployment and Operations

### 7.1 Docker

- Provide a `Dockerfile` that builds the service.
- Expose the documented port (e.g., 8000).
- Bind to `0.0.0.0`.
- No secrets baked in; use environment variables.
- Provide a `docker run` command in README.

### 7.2 Environment Variables

| Variable      | Description                 | Required                |
| ------------- | --------------------------- | ----------------------- |
| `LLM_API_KEY` | API key for LLM provider    | Yes (if using external) |
| `LLM_MODEL`   | Model name (e.g., `gpt-4`)  | Yes                     |
| `PORT`        | Service port (default 8000) | No                      |

### 7.3 Health Check

- `GET /health` returns `{"status": "ok"}` immediately when service is ready.

---

## 8. Testing and Validation

### 8.1 Public Sample Cases

- Load the provided `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`.
- For each case, POST `case.input` to `/optimize-energy`.
- Compare `directive_interpretation` against expected (semantic match, not byte-for-byte).
- Replay `hourly_plan` against constraints and recalculate totals.
- Verify cost is within tolerance of reference optimal cost.

### 8.2 Unit Tests

- Test guardrail validation with valid and invalid LLM outputs.
- Test optimizer with simple scenarios (e.g., no directives, single directive).
- Test battery dynamics and end-of-day neutrality.

### 8.3 Integration Tests

- Test full pipeline with mocked LLM and real optimizer.
- Test API endpoints with FastAPI TestClient or equivalent.
- Test error handling for malformed JSON, missing fields, invalid hours.

---

## 9. Documentation and Submission

### 9.1 README

Must include:

- Project description
- Architecture overview (LLM → Guardrails → Optimizer)
- Setup instructions (clone, env vars, install, run)
- Model/provider and LLM role
- Guardrails explanation
- Optimizer/solver used
- Exact run command
- `curl` examples for `/health` and `/optimize-energy`
- Public sample test command
- Dependencies and limitations
- Docker pull/run instructions
- No secrets

### 9.2 Video

- Max 3 minutes.
- Explain problem, architecture, solution flow, LLM/guardrail/optimizer pipeline, and how to run/test.
- Used only for tie-breaking.

---

## 10. Evaluation Alignment

| Rubric Category                                | Points | How to Satisfy                                                                                               |
| ---------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------ |
| LLM Directive Interpretation                   | 25     | Accurate parsing of notes, correct directive types, hours, factors, `no_op` handling, paraphrase robustness. |
| Directive Application & Constraint Correctness | 25     | Correctly apply directives to optimizer; satisfy energy balance, battery limits, end-of-day neutrality.      |
| Optimization Quality                           | 10     | Minimize cost; valid schedules only.                                                                         |
| API Contract & Schema                          | 10     | Exact endpoints, request/response schemas, `scenario_id` echo.                                               |
| Performance & Reliability                      | 10     | Health check, p95 latency < 5s, no 5xx on valid requests, controlled error handling.                         |
| Deployment & Docker Fallback                   | 10     | Live endpoint reachable, Docker image pullable and reaches `/health`.                                        |
| Documentation & Local Reproducibility          | 10     | Self-contained README, clean local quickstart, public-sample test procedure.                                 |

---

## 11. Assumptions and Dependencies

- LLM provider is available during judging.
- Solver (e.g., CBC) is installed in Docker image.
- Public sample cases are representative but not exhaustive.
- Hidden cases may paraphrase directives; LLM must generalize.

---

## 12. Glossary

| Term                      | Definition                                                  |
| ------------------------- | ----------------------------------------------------------- |
| **Directive**             | Structured rule extracted from an operator note.            |
| **Guardrail**             | Deterministic validation of LLM output.                     |
| **Effective Solar**       | Solar available after applying `solar_reduction`.           |
| **End-of-Day Neutrality** | Final battery energy equals initial battery energy.         |
| **No-Op**                 | Directive indicating the note does not affect the schedule. |

---

**End of PRD**
