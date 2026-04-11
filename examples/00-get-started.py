from __future__ import annotations

from ara_sdk import App

app = App(
    "Get Started",
    project_name="get-started",
    description="Smallest possible Ara SDK app with one entrypoint agent.",
)


@app.agent(
    id="hello-agent",
    entrypoint=True,
    task="Reply with a short friendly greeting.",
)
def hello_agent():
    """Basic entrypoint agent."""


@app.local_entrypoint()
def local(input_payload: dict[str, str]):
    name = str((input_payload or {}).get("name") or "").strip() or "world"
    return {
        "ok": True,
        "message": f"Hello, {name}!",
        "agent_id": "hello-agent",
    }
