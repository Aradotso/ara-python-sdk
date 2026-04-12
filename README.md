# Ara Python SDK

Public Python SDK for building Ara apps with a decorator-first workflow style.

## Install

```bash
pip install ara-sdk
```

## Documentation

- SDK overview: <https://docs.ara.so/sdk/overview>
- SDK quickstart: <https://docs.ara.so/sdk/quickstart>
- SDK reference: <https://docs.ara.so/sdk/reference>
- Examples index: <https://docs.ara.so/examples/overview>

## Local testing (no uv)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e . pytest
python -m pytest -q
```

## Run maintained examples

All maintained examples are in `examples/` and ordered in `examples/README.md`.

```bash
cd examples
cp .env.example .env.local
# Fill ARA_API_KEY and provider keys in .env.local as needed.
```

Core flow per example:

```bash
ara deploy <example.py>
ara setup-auth <example.py> --ensure-runtime-key true
ara run <example.py> --agent <agent-id> --runtime-key "<runtime_key>" --message "hello"
```

Example-specific notes:

- `01-c-agent-skills-loading.py` uses a local custom `@skill_handler` decorator inside the tool function; it is not an `ara-sdk` primitive.
- `02-canonical-email-chat-cron.py` frontend requires `VITE_ARA_APP_ID` and `VITE_ARA_RUNTIME_KEY` in `examples/.env.local`.
- `03-async-ngrok-webhook.py` requires both a local callback receiver and ngrok.
- `07-app-schedule-decorator.py` is the static/declarative scheduling example (`@app.schedule(...)`).
- `07b-app-schedule-decorator.py` is the runtime-managed fire-and-forget scheduling example (create jobs via `automation_create`) using an apply-style helper local to the example.
- `07c-runtime-automation-manager.py` is the minimal runtime automation lifecycle manager (create/list/delete flows).

## Principles

- Public SDK is generic and provider-agnostic.
- Runtime policy, retries, and safety controls are enforced server-side.
- Optional integrations (Cal.com, CRM, etc.) live in examples, not in the core SDK package.

## Quickstart

```python
from ara_sdk import App, Secret, fastapi_endpoint, invoke, runtime, schedule
import os

app = App(
    "investor-meeting-booking",
    runtime_profile=runtime(
        secrets=[
            Secret.from_dotenv(),
            Secret.from_dict({"OPENAI_API_KEY": os.environ["OPENAI_API_KEY"]}),
        ],
    ),
)

@app.tool()
def send_email(to: str, subject: str, body: str) -> dict:
    """Send one email."""
    return {"ok": True, "to": to, "subject": subject}

DAILY_FOLLOWUPS = schedule.cron(
    expr="0 13 * * 1-5",
    timezone="UTC",
    run=invoke.agent("booking_coordinator", input={"message": "Send pending follow-ups."}),
)

@app.agent(
    entrypoint=True,
    skills=["send_email", "automation_create", "automation_list", "automation_delete"],
    schedules=[DAILY_FOLLOWUPS],
)
def booking_coordinator(payload: dict) -> str:
    """Coordinate scheduling requests."""
    _ = payload if isinstance(payload, dict) else {}
    return "Coordinate scheduling requests."


@app.agent()
@fastapi_endpoint(method="POST", path="/webhooks/inbound", auth="none")
def inbound_webhook_agent(payload: dict) -> str:
    """Handle inbound FastAPI endpoint payloads."""
    _ = payload if isinstance(payload, dict) else {}
    return "Handle inbound webhook payloads."
```

`App(...)` takes the DNS-safe project name as its first argument.
Use lowercase letters, digits, and hyphens only (no underscores), e.g. `my-app-1`.
You can pass it positionally (`App("my-app-1")`) or as a keyword (`App(project_name="my-app-1")`).

```bash
ara auth login
export OPENAI_API_KEY="your_provider_key"

ara deploy app.py
ara setup-auth app.py
ara run app.py --agent booking_coordinator --message "Need 3 slots next week"
ara run app.py --agent booking_coordinator --input-json '{"request":"Need 3 slots next week","context":{"caller":"cli"}}'
ara run-async app.py --agent booking_coordinator --message "Need 3 slots next week" --response-mode poll
ara logs app.py
ara events app.py --event-type channel.web.inbound --channel web --message "hello"
ara setup app.py
```

Runtime introspection and control (user API key auth, no app script required):

```bash
ara runtime capabilities --session sess-123
ara runtime skills list --session sess-123
ara runtime tools list --session sess-123 --kind builtin
ara runtime tools execute --session sess-123 --tool exec --arg command="ls -la"
ara runtime control actions --session sess-123
ara runtime control call --session sess-123 --action list_windows
ara runtime control call --session sess-123 --action launch_app --arg id=browser --arg url=https://mail.google.com
ara runtime session start
ara runtime session status
ara runtime session stop
```

`ara logs app.py` streams live runtime events for the app across all active runs.
Each line includes timestamp + run id + event type. To persist output, use shell piping:

```bash
ara logs app.py | tee app.logs
```

## Environment

- `ARA_API_KEY`: optional long-lived control-plane key
  - Preferred local workflow: `ara auth login` (stores JWT + refresh token in `~/.ara/credentials.json`).
  - Google OAuth-only accounts can run `ara auth login --api-key <ARA_API_KEY>` to store an existing API key instead of using password login.
  - CI/headless workflows should continue to set `ARA_API_KEY`.
- `ARA_API_BASE_URL`: optional API override (defaults to production API)
- `ARA_RUNTIME_KEY`: optional runtime key override for `run/events`
- `ARA_APP_HEADER_KEY`: optional app header key override (`X-Ara-App-Key`) for `run/events/run-async/run-status`
  - Prefer running `ara setup-auth app.py` to mint keys and then export them.
  - Set `ARA_APP_HEADER_KEY` only when explicitly using `X-Ara-App-Key` mode.

Local bootstrap helper:

- `ara auth login`:
  - fetches Supabase auth config from `/auth/cli/config`
  - opens browser OAuth login (Google by default) with PKCE and localhost callback
  - exchanges callback auth code for Supabase access + refresh token
  - stores access + refresh token locally and auto-refreshes when needed
  - default callback URL is `http://127.0.0.1:53682/auth/callback` (allowlist it in Supabase); set `ARA_CLI_OAUTH_PORT` to override
  - alternative: `ara auth login --api-key <ARA_API_KEY>` stores an existing control-plane key locally (useful for Google OAuth-only users)
- `ara setup-auth app.py`:
  - resolves `app_id` by app slug
  - ensures a runtime key exists (optional)
  - creates `/apps/{app_id}/x-keys` key when missing
  - returns both `runtime_key` and `app_header_key` in command output

## Runtime env and secrets

`runtime(...)` supports:

- `env`: plain runtime environment values (`runtime_profile.env`)
- `secrets`: ordered secret references (`runtime_profile.secret_refs`)

### Container bootstrap (remote, Modal-style)

For container-side setup, declare startup bootstrap commands in `runtime(...)` and run them inside the
provisioned app sandbox. This keeps build/bootstrap logic in the cloud container instead of depending on
local `uv` workflows.

```python
from ara_sdk import App, entrypoint, local_file, runtime

app = App(
    "research-assistant",
    runtime_profile=runtime(
        image="python:3.12-slim",
        files=[
            local_file("./scripts/bootstrap.sh", path="scripts/bootstrap.sh", executable=True),
        ],
        startup=entrypoint("scripts/bootstrap.sh"),
    ),
)
```

`python_packages` / `node_packages` remain part of runtime profile metadata, but if you need deterministic
install behavior today, use startup bootstrap commands in the container.

For observability, run warmup and stream runtime logs:

```bash
ara deploy app.py --warm true
ara logs app.py
```

Warmup/run lifecycle logs are emitted as `run.warmup.*` and `run.*`; startup failures are surfaced into
those logs with command error previews.

Secret helper options:

- `Secret.from_name(name, required_keys=None)` (reference only)
- `Secret.from_dict(env_dict)` (auto-named from key set)
- `Secret.from_dotenv(filename=".env")` (auto-named from key set)
- Migration note: `Secret.from_dict(...)` and `Secret.from_dotenv(...)` no longer accept `name=` or `required_keys=`.
  Use `Secret.from_name(...)` when you need explicit naming/required-key constraints.

Deploy behavior:

- Local secret sources sync to `/apps/{app_id}/secrets` before warmup.
- When `runtime(secrets=[...])` is present, deploy reconciles the remote app secret set to match those refs (stale secrets are removed).
- Secret references remain in manifest; plaintext values are not embedded in app manifest payloads.

## Multi-sandbox proposal shape

The SDK can now declare sandbox placement and spawn intent in the manifest:

- `policy`: `shared` | `dedicated` | `ephemeral` | `inherited`
- `key`: logical sandbox selector used by runtime placement
- `spawn`: optional child-sandbox controls (`to`, `max_depth`, `max_children_per_parent`, `max_total_child_sessions_per_run`, `ephemeral_ttl_minutes`, `child_policy`, `child_runtime`)

Example:

```python
sandbox(
    policy="dedicated",
    key="research-planner",
    allow_spawn=True,
    spawn_to=["deep-researcher", "verifier"],
    max_spawn_depth=3,
    max_children_per_parent=4,
    max_total_child_sessions_per_run=10,
    ephemeral_ttl_minutes=5,
    child_policy="ephemeral",
    child_runtime=runtime(memory_mb=1024),
)
```

Backward compatibility is preserved by default. Non-shared placement only activates when invocation input explicitly opts in:

- `use_additional_sandbox=true`, or
- `sandbox.enable_additional_sandbox=true`

## Scheduling model

Use one schedule shape everywhere:

- `@app.schedule(...)` for static declarations directly on `@app.agent` or `@app.tool`
- `schedule.cron(...)` / `schedule.every(...)` remain supported for explicit schedule objects
- `invoke.agent(...)` / `invoke.tool(...)` for schedule targets
- `scheduler.create(spec)` for dynamic runtime automation payloads

`@app.schedule(...)` supports:

- `cron="0 9 * * 1-5"` for cron schedules
- `at=[...]` for planned slots:
  - `HH:MM` for daily UTC wall-clock runs
  - `2026-04-11T16:45:00Z` for one-shot UTC timestamps
  - 5-field cron strings when you want explicit control
- multiple triggers in one decorator (for example `cron` + `at`)
- one-shot ISO timestamps are represented as cron + one-shot metadata; if the slot is already past when deployed, first execution can occur on the next yearly recurrence

For runtime-managed schedules (not declared in code), see:

- `examples/07b-app-schedule-decorator.py`
- `examples/07c-runtime-automation-manager.py`

Example:

```python
@app.schedule(cron="0 9 * * 1-5", at=["14:30", "2026-04-11T16:45:00Z"])
@app.agent()
def ops_agent(input: dict) -> str:
    return "Run operational checks."


@app.schedule(cron="0 * * * *")
@app.tool()
def cleanup_cache(path: str = "/tmp/cache") -> dict:
    return {"ok": True, "path": path}
```

## JSON runtime input contract

Agent invocation input is JSON-first.

- `invoke.agent(..., input=<json>)` now accepts any JSON-serializable payload.
- The SDK does not enforce a fixed envelope shape; callers can pass any keys they want.
- `python app.py run` and `python app.py run-async` support `--input-json` for direct JSON object input (inline string or `@path/to/file.json`).

Example:

```python
from ara_sdk import invoke

invoke.agent(
    "title_case_agent",
    input={
        "text": "hello world",
        "mode": "probe",
        "context": {"caller_agent": "planner", "trace_id": "run_123"},
    },
)
```

## Prompt-based agent contract

`@app.agent(...)` always records the agent function source in the manifest so runtimes can build per-run system instructions from JSON input.

- Agent functions must accept exactly one input parameter (JSON payload) and return `str` (or omit the return annotation).
- Define prompt behavior in the function body by returning the instruction string.
- `task=` / `instructions=` are no longer part of the public agent decorator API.

## Examples

See `examples/` for optional integrations and demo projects:

- `examples/00-get-started.py` (smallest possible app)
- `examples/01-a-agent-skills-loading.py`, `examples/01-b-agent-skills-loading.py`, `examples/01-c-agent-skills-loading.py`
- `examples/02-canonical-email-chat-cron.py` (+ frontend assets in `examples/frontend/02-canonical-email-chat-cron/`)
- `examples/03-async-ngrok-webhook.py` (+ helper scripts)
- `examples/04-calcom-booking.py`
- `examples/05-a-framework-adapters-langgraph.py`
- `examples/05-b-framework-adapters-agno.py`
- `examples/06-programmatic-secrets-redeploy.py` (live probe via `examples/06-programmatic-secrets-redeploy-test.py`)
- `examples/07-app-schedule-decorator.py` (first-class `@app.schedule(...)` usage on agent + tool)
- `examples/07b-app-schedule-decorator.py` (runtime-managed "fire-and-forget" schedule using an apply-style helper)
- `examples/07c-runtime-automation-manager.py` (minimal runtime automation create/list/delete agent flows)

## Security

- Never commit API keys, runtime keys, or provider secrets.
- Keep provider-specific credentials in environment variables.

## License

This repository is source-available under a strict proprietary license.
Unauthorized copying, redistribution, or derivative works are prohibited.
See `LICENSE` for full terms.
