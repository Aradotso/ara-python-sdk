from ara_sdk import App, git_artifact, langgraph_adapter, sandbox

app = App("framework-adapter-minimal-langgraph")


@app.agent(
    entrypoint=True,
    sandbox=sandbox(max_concurrency=2),
    runtime={
        "adapter": langgraph_adapter(
            entrypoint="python3 worker.py",
            artifact=git_artifact("https://github.com/langchain-ai/langgraph", ref="main"),
            env={"PYTHONUNBUFFERED": "1"},
        ),
    },
)
def message_router(input: dict) -> str:
    return "Route incoming messages to the framework worker."
