from ara_sdk import App, git_artifact, langgraph_adapter, sandbox

app = App(
    "Framework Adapter Minimal (LangGraph)",
    project_name="framework-adapter-minimal-langgraph",
    description="Minimal agent example using langgraph_adapter.",
)


@app.agent(
    id="message-router",
    entrypoint=True,
    prompt_factory=True,
    sandbox=sandbox(max_concurrency=2),
    runtime={
        "adapter": langgraph_adapter(
            entrypoint="python3 worker.py",
            artifact=git_artifact("https://github.com/langchain-ai/langgraph", ref="main"),
            env={"PYTHONUNBUFFERED": "1"},
        ),
    },
)
def message_router(payload: dict) -> str:
    """Build runtime routing instructions from JSON input payload."""
    input_payload = payload if isinstance(payload, dict) else {}
    route = str(input_payload.get("route") or "").strip().lower()
    if route:
        return f"""
Route incoming messages to the framework worker using route='{route}'.
""".strip()
    return """
Route incoming messages to the framework worker.
""".strip()
