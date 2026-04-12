# Ara Python SDK

Ara is a managed platform for building and running long-lived AI agents in the cloud.

The `ara-sdk` is the Python layer for defining those agents, tools, schedules, and endpoints as code. Ara runs the cloud runtime and operations, while the SDK gives you composable primitives to build novel 24/7 managed workflows.

## Overview

Main SDK primitives:

- `App(...)`: define one deployable Ara app.
- `@app.agent(...)`: define agent behavior and entrypoints.
- `@app.tool(...)`: expose deterministic functions as callable tools.
- `@app.schedule(...)`: trigger agents/tools on cron or fixed times.
- `@fastapi_endpoint(...)`: expose HTTP endpoints backed by app agents.
- `runtime(...)` + `Secret`: declare runtime env, startup, and secrets.

These primitives can be combined to run always-on assistants, scheduled automation jobs, and API-triggered workflows in one app.

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
from ara_sdk import App, fastapi_endpoint

app = App("support-ops")

@app.tool()
def send_email(to: str, subject: str, body: str) -> dict:
    return {"ok": True, "to": to, "subject": subject}

@app.schedule(cron="0 * * * *")
@app.agent(entrypoint=True, skills=["send_email"])
def support_coordinator(input: dict) -> str:
    _ = input if isinstance(input, dict) else {}
    return "Handle support operations and send follow-ups."

@app.agent()
@fastapi_endpoint(method="POST", path="/webhooks/inbound", auth="none")
def inbound_webhook(input: dict) -> str:
    _ = input if isinstance(input, dict) else {}
    return "Process inbound webhook events."
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

## FAQ

### What is the boundary between Ara and `ara-sdk`?
Ara is the managed control/runtime plane (execution, lifecycle, policy, observability), while `ara-sdk` is the authoring layer for app behavior (agents, tools, schedules, endpoints). You define Python app logic; Ara handles runtime operations you would otherwise run as custom infrastructure.

### Does “24/7 runtime” mean I pay for permanently hot compute?
Not inherently. The runtime is always available as the service boundary, but sandbox/task compute can activate on demand from schedules, events, and API calls. Practical cost and footprint depend on your trigger frequency and runtime policy. _Note: today usage is billed under your existing Pro or Ultra subscription._

### How should I think about `@app.agent`, `@app.tool`, and `@app.schedule` (and when to use each)?
Use `@app.agent` for interpretation/planning, `@app.tool` for deterministic and reusable capability execution, and `@app.schedule` for time-based triggers. Combined, they let you build always-on workflows that handle operational automation, filesystem/computer-style tasks, and multi-agent delegation beyond chat-only use cases.

### How do I handle secrets and environment safely at scale?
Use runtime secret primitives (`Secret` + `runtime(...)`) so credentials are injected at runtime instead of committed in code. This keeps apps portable across environments and reduces secret exposure in source control.

### Do I need public endpoints to use Ara?
No. Public endpoints are optional ingress, not a requirement for running Ara apps. You can operate entirely through CLI-triggered runs, schedules, and internal automation flows, then expose HTTP/webhook entrypoints with `@fastapi_endpoint(...)` only when external systems (or users) need to call in directly.
