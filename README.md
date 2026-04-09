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

@app.subagent(handoff_to=["calendar-strategist"], sandbox=sandbox())
def booking_coordinator(event=None):
    """Coordinate scheduling requests."""

@app.hook(id="daily-followups", event="scheduler.followups", schedule=cron("0 13 * * 1-5"))
def daily_followups():
    """Send pending follow-ups."""

if __name__ == "__main__":
    run_cli(app)
```

```bash
export ARA_ACCESS_TOKEN="your_user_jwt"
export OPENAI_API_KEY="your_provider_key"

python app.py deploy
python app.py run --workflow booking-coordinator --message "Need 3 slots next week"
python app.py events --event-type channel.web.inbound --channel web --message "hello"
python app.py setup
```

## Environment

- `ARA_ACCESS_TOKEN`: user JWT for control plane
- `ARA_API_BASE_URL`: optional API override (defaults to production API)
- `ARA_RUNTIME_KEY`: optional runtime key override for `run/events`
  - In the Ara app, open `Settings -> System`, then use **Auth Token -> Copy Access Token**.
  - Paste that value into `ARA_ACCESS_TOKEN` before running SDK commands.

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
