from ara_sdk import App, agno_adapter, run_cli, sandbox, tarball_artifact

app = App(
    "Framework Adapter Minimal (Agno)",
    project_name="framework-adapter-minimal-agno",
    description="Minimal subagent example using agno_adapter.",
)


@app.subagent(
    id="followup-writer",
    workflow_id="draft-followup",
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


if __name__ == "__main__":
    run_cli(app)
