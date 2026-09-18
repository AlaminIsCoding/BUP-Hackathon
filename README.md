# GridWise LLM-Assisted Energy Optimization API

GridWise converts operator notes and a 24-hour energy scenario into a validated
battery, solar, and grid schedule. The service combines an OpenAI-compatible LLM
with deterministic guardrails, a PuLP/CBC MILP optimizer, and an independent
replay validator.

## Submission Artifacts

| Item | Reference |
| --- | --- |
| API base URL | `https://bup-hackathon-rwx8.onrender.com` |
| Health check | `https://bup-hackathon-rwx8.onrender.com/health` |
| GitHub repository | `https://github.com/AlaminIsCoding/BUP-Hackathon` |
| Docker tag | `ghcr.io/alaminiscoding/bup-hackathon:v1.0.0` |
| Immutable Docker image | `ghcr.io/alaminiscoding/bup-hackathon@sha256:409d4e610c805d728574ef390550d8fc63da1b8d29c3362f165417da1ec39a66` |

The API is deployed on Render. The GHCR package is public and the image digest
was resolved anonymously after the GitHub Actions build completed. The
repository does not contain an architecture video. Submit one separately if
the event form requires it.

## Evaluator Quick Start

Check the live service:

```bash
curl https://bup-hackathon-rwx8.onrender.com/health
# {"status":"ok"}
```

Pull the immutable fallback image:

```bash
docker pull ghcr.io/alaminiscoding/bup-hackathon@sha256:409d4e610c805d728574ef390550d8fc63da1b8d29c3362f165417da1ec39a66
```

The image can serve `/health` without an LLM key. Supply the provider variables
at runtime to interpret operator notes.

## How It Works

1. FastAPI validates the request shape and the 24-hour input.
2. The LLM maps each operator note to one of six structured directives.
3. Guardrails validate the untrusted LLM output and apply `no_op` on failure.
4. PuLP/CBC solves the least-cost feasible schedule.
5. The replay validator checks constraints and recomputes totals independently.
6. FastAPI returns the validated schedule, totals, and a deterministic summary.

### Supported directives

- `solar_reduction`
- `minimum_battery_reserve`
- `no_charge_window`
- `no_discharge_window`
- `max_grid_window`
- `no_op`

## Architecture

| Module | Responsibility |
| --- | --- |
| `app/main.py` | FastAPI application and request pipeline. |
| `app/schemas.py` | Pydantic v2 request, response, and enum definitions. |
| `app/config.py` | Provider presets and environment-backed settings. |
| `app/llm/prompt.py` | System prompt and directive examples. |
| `app/llm/interpreter.py` | Chat Completions call, retries, parsing, and fallback. |
| `app/guardrails.py` | Deterministic directive validation and normalization. |
| `app/optimizer.py` | PuLP/CBC mixed-integer optimization model. |
| `app/replay.py` | Independent schedule validation and totals calculation. |
| `app/summary.py` | Deterministic human-readable plan summary. |
| `scripts/run_public_samples.py` | Offline and live public-case verification harness. |
| `.github/workflows/publish-image.yml` | Build, smoke-test, and publish workflow. |
| `render.yaml` | Render Blueprint for the deployed service. |

## API

### `GET /health`

Returns a liveness response without calling the LLM or optimizer:

```json
{"status":"ok"}
```

### `POST /optimize-energy`

The request contains:

- `scenario_id`: caller-defined scenario identifier.
- `operator_notes`: one to three non-empty notes.
- `hours`: exactly 24 entries covering hours `0` through `23`.
- `battery`: capacity, initial energy, minimum energy, and hourly charge and
  discharge limits.

The canonical public request cases are in
`.docs/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`.

The response includes:

- `directive_interpretation`: validated directive for each note.
- `hourly_plan`: grid, solar, battery action, and battery state for every hour.
- `total_grid_kwh`, `total_cost_bdt`, and `peak_grid_kwh`.
- `plan_summary`: deterministic summary generated from the validated result.

To test the deployed endpoint with the first canonical case:

```bash
python -c "import json,httpx; p=json.load(open('.docs/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json'))['cases'][0]['input']; r=httpx.post('https://bup-hackathon-rwx8.onrender.com/optimize-energy',json=p,timeout=60); print(r.status_code); print(r.text)"
```

The deployed service has been tested with a directive-bearing request and
returned `200` with `solar_reduction` applied to the requested hours.

## Local Development

### Requirements

- Python 3.11 or newer
- A CBC solver. PuLP supplies one locally; the Docker image installs `coinor-cbc`.
- An OpenAI-compatible Chat Completions provider for live LLM interpretation.

Create an environment and install dependencies:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set a provider key before making live requests.
The application loads `.env` locally through `python-dotenv`. The file is
git-ignored and excluded from Docker builds.

Start the API locally:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The `uvicorn` command is the local development command. Render uses the same
application entrypoint with its assigned `$PORT`.

## LLM Configuration

The interpreter uses the OpenAI-compatible Chat Completions protocol. Select a
provider with `LLM_PROVIDER`; explicit base URL, model, and generic key values
override the provider preset.

| Variable | Description | Default |
| --- | --- | --- |
| `LLM_PROVIDER` | `openai`, `openrouter`, `gemini`, `ollama`, or `custom` | `openai` |
| `LLM_BASE_URL` | OpenAI-compatible API base URL | Provider preset |
| `LLM_MODEL` | Model identifier | Provider preset |
| `LLM_API_KEY` | Generic key override | Empty |
| `OPENROUTER_API_KEY` | OpenRouter key | Empty |
| `OPENAI_API_KEY` | OpenAI key | Empty |
| `GEMINI_API_KEY` | Gemini key | Empty |
| `OLLAMA_API_KEY` | Optional Ollama key | Empty |
| `PORT` | HTTP bind port | `8000` |

The deployed Render configuration uses:

```env
LLM_PROVIDER=openrouter
LLM_MODEL=deepseek/deepseek-v4.1-flash
OPENROUTER_API_KEY=<secret>
```

Never commit a real API key. If the provider is unavailable or returns invalid
data, the service keeps the request valid by using `no_op` for the affected
notes. It never writes raw model responses or secrets to logs.

## Render Deployment

`render.yaml` defines the deployed Python web service:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- Plan: Render free web service

The free plan can sleep while idle, so the first request after inactivity may
take longer. Render supplies `PORT`; do not hard-code it in the service.

## Docker and GitHub Actions

The Dockerfile installs the Linux CBC solver, copies the application and sample
harness, binds to `0.0.0.0`, and honors `PORT`. It does not copy `.env` or any
other local secret.

Run the published image locally:

```bash
docker run --rm -p 8000:8000 --env-file .env \
  ghcr.io/alaminiscoding/bup-hackathon@sha256:409d4e610c805d728574ef390550d8fc63da1b8d29c3362f165417da1ec39a66
```

The GitHub Actions workflow builds on a GitHub-hosted runner, runs the image
health check, and publishes tag and commit references to GHCR. Docker is not
required on the development machine. To publish a future version, create a new
version tag instead of reusing `v1.0.0`:

```bash
git tag v1.0.1
git push origin v1.0.1
```

The workflow publishes references in this form:

```text
ghcr.io/<github-owner>/<repository>:v1.0.1
ghcr.io/<github-owner>/<repository>:sha-<commit-sha>
```

Use the immutable digest printed by the completed workflow for a submission.

## Verification

Run the complete offline test suite:

```bash
pytest
```

Run the public cases without network access:

```bash
python scripts/run_public_samples.py --offline
```

Run all public cases against a running service. This sends one LLM request per
case and may consume provider quota:

```bash
python scripts/run_public_samples.py --live --base-url http://localhost:8000
```

The offline suite and public sample harness validate the optimizer and replay
logic. The live harness also validates the service's returned hourly plans.

## Design Notes and Limitations

- The battery model is lossless, matching the reference schedules.
- Solar factors multiply when multiple directives affect the same hour.
- Reserve directives use the highest requested reserve.
- Grid caps use the lowest requested cap.
- No-charge and no-discharge windows are combined.
- The optimizer has an 8-second solver limit.
- The LLM interpretation path has a 21-second total budget, keeping normal
  requests within the 30-second service target.
- LLM work runs in a worker thread so concurrent requests do not block the
  FastAPI event loop.
- A provider outage degrades note interpretation to `no_op`; it does not produce
  an unvalidated schedule.

## Dependencies and Credits

The project uses FastAPI, Uvicorn, Pydantic, HTTPX, PuLP/CBC, python-dotenv, and
pytest. The directive logic, guardrails, optimization model, replay validator,
and public sample harness are original project code.
