from ara_sdk import App, agno_adapter, sandbox, tarball_artifact

app = App(
    "Framework Adapter Minimal (Agno)",
    project_name="framework-adapter-minimal-agno",
    description="Minimal agent example using agno_adapter.",
)


@app.agent(
    id="followup-writer",
    entrypoint=True,
    task="Draft concise follow-up text for pending threads.",
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
def followup_writer():
    """Draft followups."""


@app.local_entrypoint()
def local(input_payload: dict[str, str]):
    adapter = app.manifest["agent"]["subagents"][0]["runtime"]["adapter"]
    return {
        "ok": True,
        "framework": adapter["framework"],
        "transport": adapter["transport"],
        "entrypoint": adapter["entrypoint"],
        "message": input_payload.get("message", "hello from agno example"),
    }

