---
name: ara-sdk
description: Build and run long-lived AI agents on the Ara cloud runtime in Python — define automations, custom @ara.tool functions, secrets, and connectors, then deploy/run/schedule via the `ara` CLI.
triggers:
  - build an ara automation
  - create an ara agent
  - deploy a python agent to ara
  - schedule a recurring ara automation
  - add a custom tool to my ara agent
  - connect gmail to imessage with ara
  - use ara connectors in python
  - run an agent 24/7 in the cloud with ara
---

# ara-sdk — Ara Python SDK

> Skill by [ara.so](https://ara.so) — Daily 2026 Skills collection.

`ara-sdk` is the Python authoring layer for [Ara](https://ara.so), a managed cloud runtime for long-lived AI agents. You write a small Python file with one `ara.Automation(...)` declaration plus optional `@ara.tool` functions; Ara handles execution, scheduling, secrets, sandboxing, and connector integrations (Gmail, iMessage, Slack, etc).

This skill teaches an AI coding agent how to author, deploy, run, and schedule Ara automations using the public SDK (`pip install ara-sdk`, `import ara_sdk as ara`) — based on the latest examples in [Aradotso/ara-python-sdk](https://github.com/Aradotso/ara-python-sdk).

---

## Mental model

Two primitives:

1. **`ara.Automation(id, system_instructions=..., tools=[...])`** — the agent declaration. One per file.
2. **`@ara.tool`** — a Python function the agent can call. Any JSON-serializable return type is fine (`dict`, `list`, `str`, `int`, `float`, `bool`, `None`).

Three tool sources at runtime:

- **Built-in tools** — file ops, shell exec, subagent spawn (always available).
- **Connector tools** — third-party integrations (Gmail, iMessage/`linq_send_message`, Slack, etc.) enabled at app.ara.so/connect. **On by default**; pass `allow_connector_tools=False` to lock down.
- **SDK custom tools** — the `@ara.tool` Python functions you ship.

Schedules (cron) are configured in the **app.ara.so UI** per automation, *not* in code, to avoid drift. CLI flags `--cron` / `--every-seconds` exist for one-shot setup at deploy time.

---

## Install

```bash
pip install ara-sdk
```

Requires Python ≥ 3.10. Installs the `ara` CLI (also aliased as `ara-sdk`).

### Auth

Interactive (default — opens a browser):

```bash
ara auth login
ara auth whoami
ara auth logout
ara auth rotate    # invalidate current key, mint a new one
```

Non-interactive (CI, headless, scripts):

```bash
export ARA_API_KEY="<your_key>"
ara deploy app.py
ara run app.py
```

`ARA_API_KEY` short-circuits `ara auth login`.

---

## Hello-world automation

`hello.py`:

```python
import ara_sdk as ara
from datetime import datetime, timezone

@ara.tool
def utc_now() -> dict:
    return {"utc_time": datetime.now(timezone.utc).isoformat()}

ara.Automation(
    "hello-hourly-agent",
    system_instructions=(
        "Reply with one short hello message and include UTC time. "
        "If linq_send_message is available and a phone route is paired, "
        "send the same message there once."
    ),
    tools=[utc_now],
)
```

Deploy + run:

```bash
ara auth login          # one-time
ara deploy hello.py     # uploads + activates the automation
ara run hello.py        # triggers a single run, tails logs
```

In **app.ara.so** → automation → set cron `0 * * * *` and toggle Enabled to make it hourly.

---

## CLI reference (most-used)

```bash
ara auth login | whoami | logout | rotate
ara deploy <file.py>  [flags]
ara up     <file.py>  [flags]   # alias for deploy
ara run    <file.py>  [flags]
ara logs   <file.py>  [flags]
```

### `ara deploy` flags

| Flag | Default | Purpose |
|---|---|---|
| `--activate` | `true` | Activate after deploy |
| `--cron "<expr>"` | — | Set a cron schedule on deploy (e.g. `"0 9 * * *"`) |
| `--every-seconds N` | — | Set a fixed-interval schedule (mutex with `--cron`) |
| `--timezone` | `UTC` | Schedule TZ |
| `--rpm N` | `60` | Per-minute rate limit |
| `--warm` | `false` | Keep a warm runtime |
| `--warm-agent` | — | Specific agent id to keep warm |
| `--on-existing` | `update` | `update` or `error` if automation exists |
| `--key-name` | — | Pin a runtime key |
| `--log` | off | Tail live runtime logs after deploy |
| `--log-file <path>` | — | Append tailed logs to file |

### `ara run` flags

| Flag | Default | Purpose |
|---|---|---|
| `--stream-logs` / `--no-stream-logs` | on | Tail logs until `run.completed`/`run.failed` |
| `--stream-logs-timeout-seconds` | `45` | Max wait for terminal log line |
| `--runtime-key`, `--app-header-key` | — | Auth overrides |
| `--log-file <path>` | — | Append run logs to file |

---

## Authoring patterns

### 1. Single tool, no args

```python
import ara_sdk as ara
import datetime

@ara.tool
def hello_world() -> dict:
    return {
        "ok": True,
        "message": "hello world",
        "utc_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

ara.Automation(
    "hello-tool-agent",
    system_instructions="Use the hello_world tool when useful, then reply briefly.",
    tools=[hello_world],
)
```

### 2. Multiple tools with typed args (auto-derived JSON schema)

The SDK introspects function signatures to build the tool's JSON schema. Use type hints + a docstring; the model gets both.

```python
import ara_sdk as ara
import datetime

@ara.tool
def ping() -> dict:
    return {"ok": True, "tool": "ping"}

@ara.tool
def utc_now() -> dict:
    """Return current UTC timestamp."""
    return {"utc_time": datetime.datetime.now(datetime.timezone.utc).isoformat()}

@ara.tool
def make_briefing(topic: str, audience: str) -> dict:
    """
    Create a simple one-line briefing.

    Args:
        topic: Topic to summarize.
        audience: Audience for the briefing.
    """
    return {"briefing": f"{topic.strip()} update prepared for {audience.strip()}."}

ara.Automation(
    "metadata-tools-agent",
    system_instructions=(
        "Use tools to answer requests. Prefer ping/utc_now for deterministic "
        "checks and make_briefing for short summaries."
    ),
    tools=[ping, utc_now, make_briefing],
)
```

Supported parameter types map to JSON schema:

| Python annotation | JSON schema |
|---|---|
| `str` (or none) | `{"type": "string"}` |
| `bool` | `{"type": "boolean"}` |
| `int` | `{"type": "integer"}` |
| `float` | `{"type": "number"}` |
| `dict` / `dict[...]` | `{"type": "object"}` |
| `list` / `tuple` / `set` | `{"type": "array"}` |

Defaults are inlined. Parameters without a default become `required`.

Advanced override via the decorator:

```python
@ara.tool(
    id="custom_id",
    parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    required_env=["MY_API_KEY"],
)
def search(q: str) -> dict: ...
```

### 3. Env vars and secrets

```python
import ara_sdk as ara

@ara.tool
def read_runtime_config() -> dict:
    region = ara.env("APP_REGION", default="us-east-1")  # optional config
    sender = ara.secret("CRON_EMAIL_FROM")               # required secret
    return {"ok": True, "region": region, "from_address": sender}

ara.Automation(
    "env-secrets-agent",
    system_instructions="Use read_runtime_config and explain values in one short reply.",
    tools=[read_runtime_config],
)
```

Rules (enforced by the SDK):

- `ara.env(KEY, default=...)` — never raises; returns `default` (or `""`) if unset.
- `ara.secret(KEY)` — **raises `RuntimeError` at deploy/runtime if missing** unless you pass a `default`.
- Env keys must match `[A-Za-z_][A-Za-z0-9_]{0,127}`.
- **Reserved**: keys starting with `ARA_` or `MODAL_`, plus `SESSION_ID`, `USER_ID`, `APP_ID`. Don't use these.
- Secret values are pulled from a local `.env` / `.env.local` (or the Ara secret store) at deploy time; the CLI uploads them encrypted. **Never commit `.env` files**.

`.env.example` pattern:

```bash
ARA_API_KEY=
RESEND_API_KEY=
EXA_API_KEY=
CRON_EMAIL_FROM=noreply@example.com
DIGEST_TO=
DIGEST_TOPIC=AI agents, startups, and developer tools
```

### 4. Entrypoint script (install deps before tools run)

For pure-Python deps not bundled in the runtime:

`05-entrypoint.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
python -m pip install --quiet requests
```

`app.py`:
```python
import ara_sdk as ara

@ara.tool
def requests_version() -> dict:
    import requests
    return {"ok": True, "requests_version": requests.__version__}

ara.Automation(
    "entrypoint-agent",
    system_instructions="Use requests_version tool and reply with the installed version.",
    tools=[requests_version],
    entrypoint="./05-entrypoint.sh",   # path relative to the .py file
)
```

Make the script executable: `chmod +x 05-entrypoint.sh`.

### 5. Connectors (Gmail / iMessage / etc.)

Connectors are enabled on by default; the agent automatically sees tools like `gmail_search_emails`, `linq_send_message`, etc. once the user pairs the integration in **app.ara.so/connect**.

Minimal Gmail → iMessage relay:

```python
import ara_sdk as ara

ara.Automation(
    "gmail-to-imessage",
    system_instructions=(
        "1. Use gmail_search_emails to find the single most recent email in the inbox. "
        "2. Summarize it in 2-3 sentences (sender, subject, key point). "
        "3. Send that summary to the user via linq_send_message. "
        "Do not ask questions — just do it."
    ),
)
```

Lock down to a specific toolkit/action with `skills=[...]` (and optionally `allow_connector_tools=False` for strict mode):

```python
ara.Automation(
    "gmail-only",
    system_instructions="Search inbox, summarize, send via iMessage.",
    allow_connector_tools=False,                 # opt out of the global default
    skills=[
        ara.connectors.gmail.search_emails,      # scoped action
        ara.connectors.linq.send_message,
    ],
)
```

`ara.connectors.<toolkit>` returns every action in the toolkit. `ara.connectors.<toolkit>.<action>` scopes to one action. Both render to `connector:<toolkit>:<action>` tokens internally.

### 6. File-state across runs (sandbox FS)

The runtime sandbox has a writable filesystem persisted across runs of the same automation:

```python
import ara_sdk as ara

@ara.tool
def append_heartbeat() -> dict:
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    journal = Path("/tmp/file-journal.json")
    rows = json.loads(journal.read_text()) if journal.exists() else []
    rows.append({"utc_time": datetime.now(timezone.utc).isoformat(), "note": "heartbeat"})
    journal.write_text(json.dumps(rows, indent=2))
    return {"ok": True, "count": len(rows), "path": str(journal)}

@ara.tool
def read_recent(limit: int = 3) -> dict:
    import json
    from pathlib import Path
    journal = Path("/tmp/file-journal.json")
    rows = json.loads(journal.read_text()) if journal.exists() else []
    return {"ok": True, "recent": rows[-max(1, int(limit)):], "count": len(rows)}

ara.Automation(
    "file-journal-agent",
    system_instructions=(
        "On each run, call append_heartbeat once, then call read_recent with limit=3. "
        "Reply with the newest timestamp and total count."
    ),
    tools=[append_heartbeat, read_recent],
)
```

### 7. Real-world example: news digest with Exa + Resend

```python
import ara_sdk as ara

SYSTEM_INSTRUCTIONS = (
    "On every run, call fetch_news_digest, then call send_news_digest. "
    "Do not answer until both tools have been called."
)

@ara.tool
def fetch_news_digest() -> dict:
    from exa_py import Exa
    exa = Exa(ara.secret("EXA_API_KEY"))
    topic = ara.env("DIGEST_TOPIC", default="AI agents, startups, and developer tools")
    data = exa.search(
        f"latest updates about {topic}",
        type="auto",
        num_results=5,
        contents={"highlights": {"maxCharacters": 500}},
    )
    lines = []
    for row in (data.results or [])[:5]:
        title = (getattr(row, "title", "") or "Untitled").strip()
        url = (getattr(row, "url", "") or "").strip()
        lines.append(f"- {title} ({url})" if url else f"- {title}")
    return {"ok": True, "topic": topic, "digest": "\n".join(lines) or "- No results."}

@ara.tool
def send_news_digest(subject: str, body: str) -> dict:
    import json, urllib.request
    payload = {
        "from": ara.env("CRON_EMAIL_FROM", default="alerts@yourdomain.com"),
        "to": [ara.env("DIGEST_TO", default=ara.env("CRON_EMAIL_FROM"))],
        "subject": subject.strip() or "Morning news digest",
        "text": body.strip(),
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ara.secret('RESEND_API_KEY')}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return {"ok": True, "email_id": json.loads(resp.read()).get("id")}

ara.Automation(
    "morning-news-digest",
    system_instructions=SYSTEM_INSTRUCTIONS,
    tools=[fetch_news_digest, send_news_digest],
)
```

Deploy + schedule for 9am daily UTC:

```bash
ara deploy app.py --cron "0 9 * * *" --timezone UTC
```

---

## `ara.Automation(...)` full signature

```python
ara.Automation(
    id: str,                                # required, slug-like
    *,
    system_instructions: str = "",          # the agent's prompt; defaults to "Run automation '<id>'."
    tools: list[Callable] = None,           # @ara.tool functions (auto-decorates plain callables)
    skills: list = None,                    # str names or ara.connectors.* refs to scope connector access
    allow_connector_tools: bool = True,     # set False for strict mode (only `skills=` connector actions)
    required_env: list[str] = None,         # env keys the runtime must have set
    entrypoint: str = "",                   # path to a setup script run before tools
    execution: dict = None,                 # advanced runtime overrides
)
```

Name rules: `id` becomes the automation slug. Lowercase letters, digits, hyphens; must start and end with `[a-z0-9]`; ≤ 63 chars (kebab-case). One `Automation(...)` per file is the supported pattern.

---

## Common patterns & pitfalls

- **Imperative system_instructions.** Be explicit and ordered ("1. … 2. … 3. …. Do not ask questions — just do it."). The runtime delivery model rewards step-by-step prompts.
- **Tools return JSON, not strings.** Returning a `dict` keeps the model's parsing reliable and shows up cleanly in logs.
- **Lazy imports inside tools.** Keep top-level imports minimal; `import` heavy deps inside the `@ara.tool` body so deploy bundling stays fast.
- **No lambdas / dynamically-defined tools.** The SDK reads source via `inspect.getsource`; only top-level `def` functions work.
- **No schedule code.** Set cron in app.ara.so or via `--cron`/`--every-seconds` at deploy. Don't try to schedule from Python.
- **Don't print secrets.** `ara.secret(...)` raises if missing; never log the value.
- **Version pin.** Latest published is `ara-sdk==0.1.53` (pre-1.0 — pin in production).

---

## Troubleshooting

### `Missing required secret: FOO`
You called `ara.secret("FOO")` but `FOO` isn't set in the runtime. Add it to `.env` / `.env.local` then redeploy, or pass a `default=`. Use `ara.env(...)` for non-required.

### `Invalid environment key: FOO` / `Reserved environment key`
Keys must match `[A-Za-z_][A-Za-z0-9_]{0,127}`; rename anything starting with `ARA_` or `MODAL_`, or named `SESSION_ID` / `USER_ID` / `APP_ID`.

### `@app.agent requires exactly one input parameter` / `return annotation must be str`
Internal — surfaces if you bypass `Automation(...)` and use the lower-level `@app.agent` directly. Use `Automation(...)`.

### `requires source-visible functions (no lambdas/dynamic defs)`
Define tools as module-level `def` — not `lambda`, not inside another function/closure.

### `Cannot combine --cron and --every-seconds`
Pick one scheduling mode at deploy.

### Connector tool not visible to the model
Confirm in **app.ara.so/connect** that the integration is paired for this user. If you set `allow_connector_tools=False`, you must list the action in `skills=[ara.connectors.<toolkit>.<action>]`.

### `Failed to connect` from CI
Ensure `ARA_API_KEY` is set in the CI env so the CLI doesn't try to open a browser.

### Tail logs after a stuck run
```bash
ara logs app.py --log-file /tmp/ara-run.log
```

---

## File layout

A typical Ara project:

```
my-agent/
├── app.py             # one ara.Automation(...) declaration
├── entrypoint.sh      # optional: pip install custom deps
├── .env.local         # local secrets (gitignored)
└── .env.example       # template (committed)
```

`.gitignore`:
```
.env
.env.local
__pycache__/
```

---

## Resources

- SDK source: <https://github.com/Aradotso/ara-python-sdk>
- Examples: <https://github.com/Aradotso/ara-python-sdk/tree/main/examples>
- Docs: <https://docs.ara.so/sdk/overview>
- Web app (connectors, schedules, secrets): <https://app.ara.so>
- PyPI: `pip install ara-sdk`
