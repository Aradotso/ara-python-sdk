from ara_sdk import App, sandbox

app = App(
    "Async Ngrok Webhook Demo",
    project_name="async-ngrok-webhook-demo",
    description="Minimal async run demo with webhook callbacks over ngrok.",
)


@app.agent(
    id="demo-agent",
    prompt_factory=True,
    sandbox=sandbox(max_concurrency=2),
)
def demo_agent(payload: dict) -> str:
    """Build runtime instructions for async webhook demos."""
    input_payload = payload if isinstance(payload, dict) else {}
    message = str(input_payload.get("message") or "").strip()
    if message:
        return """
Reply concisely to the provided input message.
""".strip()
    return """
Reply with a short friendly message.
""".strip()
