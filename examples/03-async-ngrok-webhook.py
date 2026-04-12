from ara_sdk import App, sandbox

app = App(
    "async-ngrok-webhook-demo",
)


@app.agent(
    sandbox=sandbox(max_concurrency=2),
)
def demo_agent(payload: dict) -> str:
    return "Reply concisely and helpfully to incoming webhook messages."
