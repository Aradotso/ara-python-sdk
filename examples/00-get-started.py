from __future__ import annotations

from ara_sdk import App

app = App(
    "get-started",
)


@app.agent(
    entrypoint=True,
)
def hello_agent(input: dict) -> str:
    return "Reply with a short friendly greeting."
