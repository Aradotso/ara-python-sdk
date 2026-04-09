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
from ara_sdk import App, Secret, cron, run_cli, runtime, sandbox

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

@app.subagent(
    handoff_to=["calendar-strategist"],
    sandbox=sandbox(
        policy="dedicated",
        key="booking-coordinator",
        allow_spawn=True,
        spawn_to=["calendar-strategist"],
        max_spawn_depth=2,
        child_policy="ephemeral",
    ),
)
def booking_coordinator(event=None):
    """Coordinate scheduling requests."""

@app.hook(id="daily-followups", event="scheduler.followups", schedule=cron("0 13 * * 1-5"))
def daily_followups():
    """Send pending follow-ups."""

if __name__ == "__main__":
    run_cli(app)
```

```bash
export ARA_API_KEY="your_long_lived_api_key"
export OPENAI_API_KEY="your_provider_key"

python app.py deploy
python app.py run --workflow booking-coordinator --message "Need 3 slots next week"
python app.py events --event-type channel.web.inbound --channel web --message "hello"
python app.py setup
```

## Environment

- `ARA_API_KEY`: long-lived user API key for control plane
- `ARA_API_BASE_URL`: optional API override (defaults to production API)
- `ARA_RUNTIME_KEY`: optional runtime key override for `run/events`
  - In the Ara app, open `Settings -> System`, then use **API Key -> Copy API Key**.
  - Paste that value into `ARA_API_KEY` before running SDK commands.
  - Legacy `ARA_ACCESS_TOKEN` is still accepted as a compatibility fallback.

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

## Adapter helper surface

The SDK also exports optional helper utilities for adapter-style app runtimes:

- `command_adapter(...)`
- `langchain_adapter(...)`
- `langgraph_adapter(...)`
- `agno_adapter(...)`
- `git_artifact(...)`
- `tarball_artifact(...)`
- `event_envelope(...)`

## Examples

See `examples/` for optional integrations and demo projects:

- `examples/calcom-booking/`
- `examples/framework-adapters/minimal_langgraph_subagent.py`
- `examples/framework-adapters/minimal_agno_subagent.py`

## Security

- Never commit API keys, runtime keys, or provider secrets.
- Keep provider-specific credentials in environment variables.

## License

This repository is source-available under a strict proprietary license.
Unauthorized copying, redistribution, or derivative works are prohibited.
See `LICENSE` for full terms.
