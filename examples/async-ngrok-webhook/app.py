from ara_sdk import App, sandbox

app = App(
    "Async Ngrok Webhook Demo",
    project_name="async-ngrok-webhook-demo",
    description="Minimal async run demo with webhook callbacks over ngrok.",
)


@app.agent(
    id="demo-agent",
    task="Reply to a short message.",
    sandbox=sandbox(max_concurrency=2),
)
def demo_agent(event=None):
    """Minimal workflow used for async run + callback demos."""

