# GridWise LLM-Assisted Energy Optimization API

An HTTP service that turns free-text operator notes plus a 24-hour energy
scenario into a validated, cost-optimal battery/grid/solar schedule. Built for
the BUP CSE Fest 2026 preliminary submission.

## What it does

1. Accepts the scenario JSON (24 hours + battery spec + 1–3 notes).
2. Interprets each note with an OpenAI-compatible LLM into one of six directives.
3. Deterministically validates/cleans the untrusted LLM output.
4. Solves a MILP for the cheapest feasible 24-hour schedule.
5. Replays the schedule to verify every constraint and recompute totals.
6. Returns the response schema defined in the Problem Statement (§10).

## Architecture

| Module | Responsibility |
| --- | --- |
| `app/schemas.py` | Pydantic v2 request/response models and enums (source of truth). |
| `app/config.py` | Environment-backed settings (secrets only from env). |
| `app/llm/prompt.py` | Strict system prompt + few-shot examples for all six directives. |
| `app/llm/interpreter.py` | Chat Completions call, retry/backoff, JSON parsing, safe fallback. |
| `app/guardrails.py` | Deterministic validation of LLM directives, `no_op` fallback. |
| `app/optimizer.py` | PuLP/CBC MILP for the 24-hour schedule. |
| `app/replay.py` | Independent constraint replay and totals recomputation. |
| `app/summary.py` | Deterministic `plan_summary` from directives + totals. |
| `app/main.py` | FastAPI app: `GET /health`, `POST /optimize-energy`. |

## Requirements

- Python 3.11
- A running CBC solver (bundled with PuLP; `coinor-cbc` is installed in Docker)
- An OpenAI-compatible Chat Completions endpoint

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in values
# `.env` is loaded automatically by app.config (python-dotenv); no manual export needed.
```

## Choosing an LLM provider

The interpreter speaks the OpenAI-compatible Chat Completions API, so you can
pick a provider with a single env var: `LLM_PROVIDER` (`openai`, `openrouter`,
`gemini`, `ollama`, or `custom`). Each preset fills in the base URL, default
model, and key env var; explicit `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`
override the preset.

| `LLM_PROVIDER` | Base URL | Default model | API-key env var |
| --- | --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` | `OPENAI_API_KEY` or `LLM_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4.1-flash` | `OPENROUTER_API_KEY` or `LLM_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` | `GEMINI_API_KEY` or `LLM_API_KEY` |
| `ollama` | `http://localhost:11434/v1` | `llama3.2` | none required (`OLLAMA_API_KEY` optional) |
| `custom` | `LLM_BASE_URL` | `LLM_MODEL` | `LLM_API_KEY` |

Copy-paste examples:

```env
# OpenRouter (cheap and reliable; preset model deepseek/deepseek-v4.1-flash)
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
# optional override: LLM_MODEL=deepseek/deepseek-v4.1-flash

# Google Gemini free API (OpenAI-compatible beta endpoint)
LLM_PROVIDER=gemini
GEMINI_API_KEY=...

# Local Ollama (no key; run `ollama pull llama3.2` first)
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2

# Any other OpenAI-compatible endpoint
LLM_PROVIDER=custom
LLM_BASE_URL=https://my-gateway.example/v1
LLM_MODEL=my-model
LLM_API_KEY=...
```

Notes: OpenRouter free tiers are rate-limited (roughly 20 req/min, 50–1000
req/day) and free Gemini prompts may be used for training. All three accept the
`response_format: {"type": "json_object"}` request; if a provider rejects JSON
mode the client retries once without it. A trailing `/v1` (or `/v1beta/openai`)
must be present on the base URL.

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `LLM_PROVIDER` | Provider preset: `openai`/`openrouter`/`gemini`/`ollama`/`custom` | `openai` |
| `LLM_BASE_URL` | Explicit OpenAI-compatible base URL override | preset |
| `LLM_MODEL` | Explicit model override | preset |
| `LLM_API_KEY` | Generic API key override (never logged/echoed) | _(empty)_ |
| `OPENROUTER_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_API_KEY` | Provider-specific keys | _(empty)_ |
| `PORT` | Bind port | `8000` |

Secrets are read only from the environment or a local `.env` (which is
git-ignored and excluded from the Docker image). If the LLM is unreachable or
unconfigured, every note safely falls back to `no_op` and the service still
returns a valid schedule. Precedence: real environment variables win over `.env`.

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Health

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### Optimize

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "DEMO-01",
    "operator_notes": ["Reduce solar by 75% from noon to 2 PM."],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 3, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 4, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 5, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 7, "demand_kwh": 90, "solar_kwh": 10, "tariff_bdt_per_kwh": 8},
      {"hour": 8, "demand_kwh": 90, "solar_kwh": 40, "tariff_bdt_per_kwh": 10},
      {"hour": 9, "demand_kwh": 90, "solar_kwh": 80, "tariff_bdt_per_kwh": 12},
      {"hour": 10, "demand_kwh": 90, "solar_kwh": 120, "tariff_bdt_per_kwh": 14},
      {"hour": 11, "demand_kwh": 90, "solar_kwh": 140, "tariff_bdt_per_kwh": 15},
      {"hour": 12, "demand_kwh": 90, "solar_kwh": 150, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 90, "solar_kwh": 140, "tariff_bdt_per_kwh": 15},
      {"hour": 14, "demand_kwh": 90, "solar_kwh": 110, "tariff_bdt_per_kwh": 14},
      {"hour": 15, "demand_kwh": 90, "solar_kwh": 70, "tariff_bdt_per_kwh": 12},
      {"hour": 16, "demand_kwh": 90, "solar_kwh": 30, "tariff_bdt_per_kwh": 10},
      {"hour": 17, "demand_kwh": 90, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 18, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 19, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 20, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 21, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 22, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 23, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6}
    ],
    "battery": {
      "capacity_kwh": 220,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

### Sample response

Real output for the request above (with `LLM_PROVIDER=openrouter`; directive text
and explanations vary by model, the schedule is a valid optimal plan):

```json
{
  "scenario_id": "DEMO-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
      "explanation": "75% reduction leaves 25% usable solar in hours 12-13."
    }
  ],
  "hourly_plan": [
    {"hour": 0,  "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 110.0},
    {"hour": 1,  "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 110.0},
    {"hour": 2,  "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 110.0},
    {"hour": 3,  "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 110.0},
    {"hour": 4,  "grid_kwh": 140.0, "solar_used_kwh": 0.0,   "battery_action": "charge",    "battery_kwh": 50.0, "battery_energy_after_kwh": 160.0},
    {"hour": 5,  "grid_kwh": 140.0, "solar_used_kwh": 0.0,   "battery_action": "charge",    "battery_kwh": 50.0, "battery_energy_after_kwh": 210.0},
    {"hour": 6,  "grid_kwh": 100.0, "solar_used_kwh": 0.0,   "battery_action": "charge",    "battery_kwh": 10.0, "battery_energy_after_kwh": 220.0},
    {"hour": 7,  "grid_kwh": 30.0,  "solar_used_kwh": 10.0,  "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 170.0},
    {"hour": 8,  "grid_kwh": 0.0,   "solar_used_kwh": 40.0,  "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 120.0},
    {"hour": 9,  "grid_kwh": 0.0,   "solar_used_kwh": 80.0,  "battery_action": "discharge", "battery_kwh": 10.0, "battery_energy_after_kwh": 110.0},
    {"hour": 10, "grid_kwh": 0.0,   "solar_used_kwh": 120.0, "battery_action": "charge",    "battery_kwh": 30.0, "battery_energy_after_kwh": 140.0},
    {"hour": 11, "grid_kwh": 0.0,   "solar_used_kwh": 140.0, "battery_action": "charge",    "battery_kwh": 50.0, "battery_energy_after_kwh": 190.0},
    {"hour": 12, "grid_kwh": 2.5,   "solar_used_kwh": 37.5,  "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 140.0},
    {"hour": 13, "grid_kwh": 5.0,   "solar_used_kwh": 35.0,  "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 90.0},
    {"hour": 14, "grid_kwh": 0.0,   "solar_used_kwh": 110.0, "battery_action": "charge",    "battery_kwh": 20.0, "battery_energy_after_kwh": 110.0},
    {"hour": 15, "grid_kwh": 0.0,   "solar_used_kwh": 70.0,  "battery_action": "discharge", "battery_kwh": 20.0, "battery_energy_after_kwh": 90.0},
    {"hour": 16, "grid_kwh": 10.0,  "solar_used_kwh": 30.0,  "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 40.0},
    {"hour": 17, "grid_kwh": 85.0,  "solar_used_kwh": 5.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 40.0},
    {"hour": 18, "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 40.0},
    {"hour": 19, "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 40.0},
    {"hour": 20, "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 40.0},
    {"hour": 21, "grid_kwh": 90.0,  "solar_used_kwh": 0.0,   "battery_action": "idle",      "battery_kwh": 0.0,  "battery_energy_after_kwh": 40.0},
    {"hour": 22, "grid_kwh": 110.0, "solar_used_kwh": 0.0,   "battery_action": "charge",    "battery_kwh": 20.0, "battery_energy_after_kwh": 60.0},
    {"hour": 23, "grid_kwh": 140.0, "solar_used_kwh": 0.0,   "battery_action": "charge",    "battery_kwh": 50.0, "battery_energy_after_kwh": 110.0}
  ],
  "total_grid_kwh": 1482.5,
  "total_cost_bdt": 9232.5,
  "peak_grid_kwh": 140.0,
  "plan_summary": "Applied solar_reduction on hour(s) 12-13 (usable factor 0.25). Total grid 1482.50 kWh, cost 9232.50 BDT, peak grid 140.00 kWh."
}
```

## Tests

```bash
pytest
```

The full suite runs offline with no network calls (LLM HTTP is mocked).

## Public sample harness

```bash
# Offline: expected directives -> optimizer + replay, compare against references
python scripts/run_public_samples.py --offline

# Live: POST each case input to a running service (service interprets notes)
python scripts/run_public_samples.py --live --base-url http://localhost:8000
```

`--offline` asserts our cost is within `0.01` BDT of the reference cost and that
totals replay cleanly. `--live` never feeds expected answers; it validates the
service's own `hourly_plan` via replay.

To POST a single sample case, extract its `input` first (the file is an object
with a `cases` array):

```powershell
$case = (Get-Content ".docs\BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json" -Raw | ConvertFrom-Json).cases[0]
$case.input | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 request.json
curl.exe -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" --data "@request.json"
```

> If the logs show `LLM interpretation failed; using no_op fallback`, the service
> is silently ignoring every note and the `--live` costs are not meaningful. Set
> `LLM_DEBUG=1` to log the raw provider response and diagnose the shape.

## Docker

The image installs `coinor-cbc`, binds `0.0.0.0`, and honors `PORT`. No secrets
are baked into the image: `.env` is excluded via `.dockerignore`, so credentials
must be supplied at run time with `--env-file` or `-e`.

Build and run locally:

```bash
docker build -t <registry>/gridwise:<tag> .
docker run -p 8000:8000 --env-file .env <registry>/gridwise:<tag>
curl http://localhost:8000/health
# {"status":"ok"}
```

### Fallback image (required submission artifact)

Push the tested image once so organizers can pull it during evaluation:

```bash
docker login
docker build -t <registry>/gridwise:<tag> .
docker push <registry>/gridwise:<tag>

# Judge fallback path, from a clean machine:
docker pull <registry>/gridwise:<tag>
docker run -p 8000:8000 --env-file .env <registry>/gridwise:<tag>
curl http://localhost:8000/health
# {"status":"ok"}
```

Replace `<registry>/gridwise:<tag>` with your real reference (Docker Hub, GHCR,
etc.), e.g. `youruser/gridwise:2026-09-18` or the immutable digest
`youruser/gridwise@sha256:...`. Required container environment variables:
`LLM_PROVIDER`, the provider key (e.g. `OPENROUTER_API_KEY`) or `LLM_API_KEY`,
and optionally `LLM_MODEL`, `LLM_BASE_URL`, and `PORT`.

The image bundles `scripts/` and `.docs/`, so the public-sample harness also runs
inside the container:

```bash
docker run --rm --env-file .env <registry>/gridwise:<tag> \
  python scripts/run_public_samples.py --offline
```

## Notes

- The battery is modeled as lossless, per the reference schedules.
- The MILP uses an 8-second solver time limit; the LLM interpretation path uses a
  21-second total budget, so the worst-case request stays under the 30s limit.
- Directive combination rules: solar factors multiply, reserves take the max,
  grid caps take the min, and no-charge/no-discharge windows union.
- LLM calls run in a worker thread, so concurrent requests do not block the
  event loop.
- No secrets, stack traces, or hard-coded public-case answers appear in
  responses, logs, or the repository.

## Dependencies & credits

Installed from `requirements.txt`: `fastapi`, `uvicorn[standard]` (server),
`pydantic` v2 (schemas/validation), `httpx` (LLM HTTP client), `pulp` + bundled
CBC (MILP solver; `coinor-cbc` installed in the image), `python-dotenv` (local
`.env` loading), and `pytest`/`pytest-mock` (tests only). The LLM is any
OpenAI-compatible Chat Completions endpoint selected via `LLM_PROVIDER`; the
code default is `openai`, while the shipped `.env.example`/our deployment selects
`openrouter`. No third-party code implements the directive logic, guardrails,
optimization model, or replay — those are original to this project.

## Known limitations

- If the model returns malformed output that survives retries, the affected note
  degrades to `no_op` rather than failing the request (safe failure).
- If a provider returns fewer directives than notes, only the valid prefix is
  kept; missing notes become `no_op`.
- Windows-local CBC is provided by PuLP; the Docker image installs the system
  `coinor-cbc` for Linux parity.
- Latency is dominated by the hosted LLM; a provider outage makes every note fall
  back to `no_op` (the service stays up and valid).
- The 3-minute video is required only as a tie-break artifact and is submitted
  separately.
