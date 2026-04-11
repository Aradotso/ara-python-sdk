from ara_sdk import App, agno_adapter, sandbox, tarball_artifact

app = App(
    "Framework Adapter Minimal (Agno)",
    project_name="framework-adapter-minimal-agno",
    description="Minimal agent example using agno_adapter.",
)


@app.agent(
    id="followup-writer",
    entrypoint=True,
    prompt_factory=True,
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
    """Build runtime follow-up instructions from JSON input payload."""
    input_payload = payload if isinstance(payload, dict) else {}
    tone = str(input_payload.get("tone") or "").strip().lower()
    if tone:
        return f"""
Draft concise follow-up text for pending threads using tone='{tone}'.
""".strip()
    return """
Draft concise follow-up text for pending threads.
""".strip()
