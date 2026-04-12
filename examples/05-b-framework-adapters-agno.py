from ara_sdk import App, agno_adapter, sandbox, tarball_artifact

app = App(
    "framework-adapter-minimal-agno",
)


@app.agent(
    entrypoint=True,
    sandbox=sandbox(max_concurrency=1),
    runtime={
        "adapter": agno_adapter(
            entrypoint="python3 worker.py",
            artifact=tarball_artifact(
                "https://example.com/agno-worker.tar.gz",
                strip_prefix="agno-worker",
            ),
            env={"AGNO_MODE": "minimal"},
        ),
    },
)
def followup_writer(payload: dict) -> str:
    return "Draft concise follow-up text for pending threads."
