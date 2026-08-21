# Ara Python SDK (deprecated)

Ara is a managed platform for building and running long-lived AI agents in the cloud.

The `ara-sdk` is the Python layer for defining those agents and tools in code using a minimal authoring model. Ara runs the cloud runtime and operations, while the SDK focuses on compact job scripts.

## Overview

To program on Ara, you compose workflows with two primitives: `ara.Job(...)` (system behavior) and `@ara.tool` (custom callable behavior registered into the app runtime).

Runtime flow:

```text
              +------------------------------+
              | Messages (iMessage/SMS/RCS)  |
              +------------------------------+
                        ^
                        |
                        v
              +----------------------------------------------+
              |         Ara Cloud: 24/7 Agent Runtime        |
              |  - Routes job runs and tool calls            |
              |  - Maintains runtime context and state       |
              |                                              |
              |  +------------------------+                  |
              |  |   Sandbox / Computer   |                  |
              |  | - File system access   |                  |
              |  | - Executes tasks/tools |                  |
              |  +------------------------+                  |
              +-------------------+--------------------------+
                                  ^
                                  |
        +-------------------------+-------------------------+
        |                         |                         |
+------------------------+ +--------------------------+ +-----------------------------+
|   Built-in tools       | |    Connector tools       | |   SDK custom tools          |
| - File browse/edit     | | - Enabled in app.ara.so  | | - Python functions via      |
| - Program execution    | | - External integrations  | |   @ara.tool                 |
+------------------------+ +--------------------------+ +-----------------------------+
```

Quickstart:

```bash
pip install ara-sdk
```

```python
import ara_sdk as ara

@ara.tool
def utc_now() -> dict:
    from datetime import datetime, timezone
    return {"utc_time": datetime.now(timezone.utc).isoformat()}

ara.Job(
    "hello-hourly-agent",
    system_instructions=(
        "Reply with one short hello message and include UTC time. "
        "If linq_send_message is available and a phone route is paired, "
        "send the same message there once."
    ),
)
```

Connector tools are enabled by default. To disable connector access for a job, pass `allow_connector_tools=False`.

Terminology:

- `Jobs`: deployable execution declarations (`ara.Job(...)`).
- `Tools`: callable capabilities (SDK Python tools, connector tools, built-in runtime tools).
- `Connectors`: external integration toolkits (for example Gmail, Slack, Calendar).
- `Secrets`: runtime credentials consumed by tool code (`ara.secret("KEY")`).
- `API keys`: control-plane/runtime auth keys used by CLI/SDK (`ARA_API_KEY`, runtime/app keys).

Non-interactive auth (no `ara auth login`) is supported via `ARA_API_KEY`:

```bash
ARA_API_KEY="<your_key>" ara deploy app.py
ara run app.py
```

Runtime CLI commands also use the same auth source (`ara auth login` credentials or `ARA_API_KEY`) and do not require separate runtime/API-session tokens:

```bash
ara runtime session start
ara runtime tools available --session <session_id>
ara runtime session exec --command "ls -la /root/.ara/workspace"
ara runtime files read --path notes/todo.txt
ara runtime files write --path notes/todo.txt --content "updated from cli"
ara runtime files upload --local ./draft.md --path notes/draft.md
ara runtime files download --path notes/draft.md --output ./draft-copy.md
```

`ara runtime files *` targets your active personal session directly, so it does not take a `--session` flag.

You can self-update the CLI with:

```bash
ara --update
```

The updater prefers global tool-manager installs (`uv`/`pipx`) when available, with `pip` fallback.


## Run maintained examples

Examples: [github.com/Aradotso/ara-python-sdk/tree/main/examples](https://github.com/Aradotso/ara-python-sdk/tree/main/examples)

## FAQ

### What is the difference between Ara and `ara-sdk`?
Ara is the managed control/runtime plane (execution, lifecycle, policy, observability), while `ara-sdk` is the authoring layer for job behavior (tools + instructions). You define Python app logic; Ara handles runtime operations you would otherwise run as custom infrastructure.

### Does “24/7 runtime” mean I pay for permanently hot compute?
Not inherently. The runtime is always available as the service boundary, but sandbox/task compute can activate on demand from schedules, events, and API calls. Practical cost and footprint depend on your trigger frequency and runtime policy. _Note: today usage is billed under your existing Pro or Ultra subscription._

### How should I think about `Job`, `@tool`, and `secret(...)`?
Use `Job(...)` as the top-level app declaration, `@tool` for deterministic capability execution, and `secret("KEY")` when a tool requires credentials. Schedules are configured in app.ara.so UI to avoid code/UI drift.

### How do connectors work in `Job(...)`?
Connector tools are enabled by default (`allow_connector_tools=True`). Set `allow_connector_tools=False` for strict mode, then explicitly scope allowed connector actions with `skills=[ara.connectors.<toolkit>[.<action>]]`.

### Do tools have to return a `dict`?
No. Tool functions can return any JSON-serializable value (`dict`, `list`, `str`, `int`, `float`, `bool`, or `None`). The runtime serializes structured values to JSON and provides tool output back to the model as text.

### How do I handle secrets and environment safely at scale?
Declare required credentials in code with `secret("KEY")` and provide values through Ara secret sync at deploy time (or pre-provisioned app secrets). Keep all secret values out of source control.

### Do I need public endpoints to use Ara?
No. The minimal SDK is job-first; public endpoint wiring is not part of this authoring surface.
