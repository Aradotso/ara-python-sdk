# Ara Python SDK

Public Python SDK for building Ara apps with a decorator-first workflow.

## Install

```bash
pip install ara-sdk
```

## Principles

- Public SDK is generic and provider-agnostic.
- Runtime policy, retries, and safety controls are enforced server-side.
- Optional integrations (Cal.com, CRM, etc.) live in examples, not in the core package.

## Quickstart

```python
from ara_sdk import App, cron, run_cli, sandbox

app = App("Investor Meeting Booker", project_name="investor-meeting-booking")

@app.subagent(handoff_to=["calendar-strategist"], sandbox=sandbox())
def booking_coordinator(event=None):
    """Coordinate scheduling requests."""

@app.hook(id="daily-followups", event="scheduler.followups", schedule=cron("0 13 * * 1-5"))
def daily_followups():
    """Send pending followups."""

if __name__ == "__main__":
    run_cli(app)
```

```bash
export ARA_API_BASE_URL="https://api.ara.so"
export ARA_ACCESS_TOKEN="your_user_jwt"

python app.py deploy
python app.py run --workflow booking-coordinator --message "Need 3 slots next week"
python app.py events --event-type channel.web.inbound --channel web --message "hello"
python app.py setup
```

## Environment

- `ARA_API_BASE_URL`: Ara API base URL
- `ARA_ACCESS_TOKEN`: user JWT for control plane
- `ARA_RUNTIME_KEY`: optional runtime key override for run/events (otherwise `.runtime-key.local` is used)

## Examples

See `examples/` for optional integrations and demo projects:

- `examples/calcom-booking/`

## Security

- Never commit API keys, runtime keys, or provider secrets.
- Keep provider-specific credentials in environment variables.

## License

This repository is source-available under a strict proprietary license.
Unauthorized copying, redistribution, or derivative works are prohibited.
See `LICENSE` for full terms.
