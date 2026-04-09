from ara_sdk import App, git_artifact, langgraph_adapter, run_cli, sandbox

app = App(
    "Framework Adapter Minimal (LangGraph)",
    project_name="framework-adapter-minimal-langgraph",
    description="Minimal agent example using langgraph_adapter.",
)


@app.agent(
    id="message-router",
    entrypoint=True,
    task="Route incoming messages to the framework worker.",
    sandbox=sandbox(max_concurrency=2),
    runtime={
        "adapter": langgraph_adapter(
            entrypoint="python3 worker.py",
            artifact=git_artifact("https://github.com/langchain-ai/langgraph", ref="main"),
            env={"PYTHONUNBUFFERED": "1"},
        ),
    },
)
def message_router():
    """Route inbound messages."""


@app.local_entrypoint()
def local(input_payload: dict[str, str]):
    adapter = app.manifest["agent"]["subagents"][0]["runtime"]["adapter"]
    return {
        "ok": True,
        "framework": adapter["framework"],
        "transport": adapter["transport"],
        "entrypoint": adapter["entrypoint"],
        "message": input_payload.get("message", "hello from langgraph example"),
    }


if __name__ == "__main__":
    run_cli(app)
