from __future__ import annotations

from ara_sdk import App, runtime

app = App("runtime-model-selector")


@app.agent(
    entrypoint=True,
    runtime=runtime(model="google/gemini-2.5-flash"),
)
def minimal_agent(input: dict) -> str:
    """You are a concise assistant. Reply with one short sentence."""
    return "You are a concise assistant. Reply with one short sentence."
