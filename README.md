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
6. Returns the response schema defined in `PRD.md` §4.3.

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

Secrets are read only from the environment. If the LLM is unreachable or
unconfigured, every note safely falls back to `no_op` and the service still
returns a valid schedule.

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

```bash
docker build -t gridwise .
docker run -p 8000:8000 --env-file .env gridwise
curl http://localhost:8000/health
# {"status":"ok"}
```

The image installs `coinor-cbc`, binds `0.0.0.0`, and honors `PORT`. No secrets
are baked into the image.

## Notes

- The battery is modeled as lossless, per the reference schedules.
- The MILP uses a 10-second solver time limit; the endpoint targets < 30s.
- Directive combination rules: solar factors multiply, reserves take the max,
  grid caps take the min, and no-charge/no-discharge windows union.
- No secrets, stack traces, or hard-coded public-case answers appear in
  responses, logs, or the repository.
