# Ara Python SDK

Ara is a managed platform for building and running long-lived AI agents in the cloud.

The `ara-sdk` is the Python layer for defining those agents and tools in code using a minimal authoring model. Ara runs the cloud runtime and operations, while the SDK focuses on compact automation scripts.

## Overview

Minimal SDK primitives:

- `@tool`: expose deterministic Python functions as callable tools.
- `secret("KEY")`: declare and read required secrets at runtime.
- `connectors.<toolkit>[.<action>]`: scope Composio-backed connector access for an automation.
- `Automation(...)`: define one deployable automation agent with tools and instructions.

This keeps authoring simple: one script, one automation declaration, optional helper metadata.

Runtime flow:

```text
Users / Clients
  (Web app, SMS, Slack, Email)
            |
            |  HTTP (bidirectional webhooks / responses)
            v
+----------------------------------------------+
|         Ara Cloud: 24/7 Agent Runtime        |
|  - Interprets prompt + workflow              |
|  - Activates sandbox when work is needed     |
+--------------------------+-------------------+
                           |
                           v
                 +------------------------+
                 |   Sandbox / Computer   |
                 | - File system access   |
                 | - Executes tasks/tools |
                 +-----------+------------+
                             |
            +----------------+----------------+
            v                                 v
 +---------------------------+   +---------------------------+
 | Custom tools / skill files|   | Subagents / other agents |
 +---------------------------+   +---------------------------+
```

Quickstart:

```python
import ara_sdk as ara

@ara.tool
def send_email(to: str, subject: str, body: str) -> dict:
    sender = ara.secret("CRON_EMAIL_FROM")
    api_key = ara.secret("RESEND_API_KEY")
    _ = (to, subject, body, api_key)
    return {"ok": True, "from": sender}

ara.Automation(
    "weekday-priority-agent",
    system_instructions="Send weekday priority digest.",
    tools=[send_email],
)
```

Connector tools are enabled by default. To disable connector access for an automation, pass `allow_connector_tools=False`.

Scoped connector example:

```python
import ara_sdk as ara

ara.Automation(
    "calendar-reader",
    system_instructions="Read and summarize upcoming calendar events.",
    allow_connector_tools=False,
    skills=[ara.connectors.google_calendar.list_events],
)
```

Non-interactive auth (no `ara auth login`) is supported via `ARA_API_KEY`:

```bash
ARA_API_KEY="<your_key>" ara deploy app.py
```

For CI/CD, you can also use the helper script:

```bash
python scripts/deploy_app.py app.py --api-key "<your_key>"
```

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

Examples: [github.com/Aradotso/ara-python-sdk/tree/main/examples](https://github.com/Aradotso/ara-python-sdk/tree/main/examples)

## FAQ

### What is the difference between Ara and `ara-sdk`?
Ara is the managed control/runtime plane (execution, lifecycle, policy, observability), while `ara-sdk` is the authoring layer for automation behavior (tools + instructions). You define Python app logic; Ara handles runtime operations you would otherwise run as custom infrastructure.

### Does “24/7 runtime” mean I pay for permanently hot compute?
Not inherently. The runtime is always available as the service boundary, but sandbox/task compute can activate on demand from schedules, events, and API calls. Practical cost and footprint depend on your trigger frequency and runtime policy. _Note: today usage is billed under your existing Pro or Ultra subscription._

### How should I think about `Automation`, `@tool`, and `secret(...)`?
Use `Automation(...)` as the top-level app declaration, `@tool` for deterministic capability execution, and `secret("KEY")` when a tool requires credentials. Schedules are configured in app.ara.so UI to avoid code/UI drift.

### How do connectors work in `Automation(...)`?
Connector tools are enabled by default (`allow_connector_tools=True`). Set `allow_connector_tools=False` for strict mode, then explicitly scope allowed connector actions with `skills=[ara.connectors.<toolkit>[.<action>]]`.

### Do tools have to return a `dict`?
No. Tool functions can return any JSON-serializable value (`dict`, `list`, `str`, `int`, `float`, `bool`, or `None`). The runtime serializes structured values to JSON and provides tool output back to the model as text.

### How do I handle secrets and environment safely at scale?
Declare required credentials in code with `secret("KEY")` and provide values through Ara secret sync at deploy time (or pre-provisioned app secrets). Keep all secret values out of source control.

### Do I need public endpoints to use Ara?
No. The minimal SDK is automation-first; public endpoint wiring is not part of this authoring surface.
