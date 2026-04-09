# Ara Python SDK

Public Python SDK for building Ara apps with a decorator-first workflow style.

## Install

```bash
pip install ara-sdk
```

## Principles

- Public SDK is generic and provider-agnostic.
- Runtime policy, retries, and safety controls are enforced server-side.
- Optional integrations (Cal.com, CRM, etc.) live in examples, not in the core SDK package.

## Quickstart

```python
from ara_sdk import App, Secret, invoke, run_cli, runtime, schedule

app = App(
    "Investor Meeting Booker",
    project_name="investor-meeting-booking",
    runtime_profile=runtime(
        env={"APP_MODE": "production"},
        secrets=[
            Secret.from_name("provider-shared", required_keys=["OPENAI_API_KEY"]),
            Secret.from_local_environ("provider-local", env_keys=["OPENAI_API_KEY"]),
        ],
    ),
)

@app.tool(id="send_email", description="Send one email.")
def send_email(to: str, subject: str, body: str) -> dict:
    return {"ok": True, "to": to, "subject": subject}

DAILY_FOLLOWUPS = schedule.cron(
    id="daily-followups",
    expr="0 13 * * 1-5",
    timezone="UTC",
    run=invoke.agent("booking-coordinator", input={"message": "Send pending follow-ups."}),
)

@app.agent(
    id="booking-coordinator",
    entrypoint=True,
    task="Coordinate scheduling requests.",
    skills=["send_email", "automation_create", "automation_list"],
    schedules=[DAILY_FOLLOWUPS],
)
def booking_coordinator():
    """Coordinate scheduling requests."""

if __name__ == "__main__":
    run_cli(app)
```

```bash
export ARA_API_KEY="your_long_lived_api_key"
export OPENAI_API_KEY="your_provider_key"

python app.py deploy
python app.py setup-auth
python app.py run --agent booking-coordinator --message "Need 3 slots next week"
python app.py run-async --agent booking-coordinator --message "Need 3 slots next week" --response-mode poll
python app.py events --event-type channel.web.inbound --channel web --message "hello"
python app.py setup
```

## Environment

- `ARA_API_KEY`: long-lived user API key for control plane
  - In the Ara app, open `Settings -> System`, then use **API Key -> Copy API Key**.
  - Paste that value into `ARA_API_KEY` before running SDK commands.
  - Legacy `ARA_ACCESS_TOKEN` is still accepted as a compatibility fallback.
- `ARA_API_BASE_URL`: optional API override (defaults to production API)
- `ARA_RUNTIME_KEY`: optional runtime key override for `run/events`
- `ARA_APP_HEADER_KEY`: optional app header key override (`X-Ara-App-Key`) for `run/events/run-async/run-status`
  - Prefer running `python app.py setup-auth` to mint/store an app header key in `.app-header-key.local`.
  - Set `ARA_APP_HEADER_KEY` only when overriding that generated key.

Local bootstrap helpers:

- `python app.py setup-auth`:
  - resolves `app_id` by app slug
  - ensures `.runtime-key.local` exists (optional)
  - creates `/apps/{app_id}/x-keys` key when missing
  - writes `.app-header-key.local` for subsequent CLI calls

## Runtime env and secrets

`runtime(...)` supports:

- `env`: plain runtime environment values (`runtime_profile.env`)
- `secrets`: ordered secret references (`runtime_profile.secret_refs`)

Secret helper options:

- `Secret.from_name(name, required_keys=None)` (reference only)
- `Secret.from_dict(name, env_dict)` (synced at deploy)
- `Secret.from_dotenv(name, filename=".env")` (synced at deploy)
- `Secret.from_local_environ(name, env_keys=[...])` (synced at deploy)

Deploy behavior:

- Local secret sources sync to `/apps/{app_id}/secrets` before warmup.
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

- `schedule.cron(...)` / `schedule.every(...)` for static declarations on `@app.agent`
- `invoke.agent(...)` / `invoke.tool(...)` for schedule targets
- `scheduler.create(spec)` for dynamic runtime automation payloads

## Examples

See `examples/` for optional integrations and demo projects:

- `examples/calcom-booking/`
- `examples/async-ngrok-webhook/`
- `examples/framework-adapters/minimal_langgraph_subagent.py` (legacy)
- `examples/framework-adapters/minimal_agno_subagent.py` (legacy)

## Security

- Never commit API keys, runtime keys, or provider secrets.
- Keep provider-specific credentials in environment variables.

## License

This repository is source-available under a strict proprietary license.
Unauthorized copying, redistribution, or derivative works are prohibited.
See `LICENSE` for full terms.
