from __future__ import annotations

import pathlib
import sys

for parent in pathlib.Path(__file__).resolve().parents:
    src_dir = parent / "src"
    if (src_dir / "ara_sdk").exists():
        sys.path.insert(0, str(src_dir))
        break

from ara_sdk import App

app = App(
    "Ara Skill Pattern 03 (Decorator Handler)",
    project_name="skill-decorator-v1",
    description="Minimal Ara SDK app: skill dispatch is implemented via a Python decorator registry.",
)


@app.tool(
    id="title_case_decorator",
    description="Convert input text by dispatching to the decorator-registered title-case handler.",
)
def title_case_decorator(text: str) -> dict:
    # Keep registry + decorator local to the tool function because ara_sdk stores
    # and executes function source for runtime tools; module globals are not guaranteed.
    handlers: dict[str, callable] = {}

    def skill_handler(name: str):
        key = str(name or "").strip().lower()
        if not key:
            raise ValueError("skill_handler(name) requires a non-empty name")

        def decorator(func):
            handlers[key] = func
            return func

        return decorator

    @skill_handler("title-case")
    def handle_title_case(value: str) -> str:
        return " ".join(word[:1].upper() + word[1:].lower() for word in value.split())

    fn = handlers["title-case"]
    return {
        "ok": True,
        "method": "decorator-handler",
        "handler": "title-case",
        "input": text,
        "result": fn(text),
    }


@app.agent(
    id="title-case-decorator-agent",
    entrypoint=True,
    prompt_factory=True,
    skills=["title_case_decorator"],
)
def title_case_agent(payload: dict) -> str:
    """Build system instructions from JSON input payload."""
    input_payload = payload if isinstance(payload, dict) else {}
    text = str(input_payload.get("text") or input_payload.get("message") or "").strip()
    mode = str(input_payload.get("mode") or "").strip().lower()
    probe_requested = mode == "probe" or text.startswith("RELIABILITY_PROBE|")
    if probe_requested:
        return """
Use title_case_decorator.
Reliability probe mode: if input starts with 'RELIABILITY_PROBE|', call title_case_decorator with the text after '|'.
No fallback.
If tool execution succeeds, return exactly:
PROBE:decorator-ok:<Title Case Text>
If tool execution fails, return exactly:
PROBE:decorator-fail
""".strip()
    return """
Use title_case_decorator for text transforms.
Always call title_case_decorator with the user's text.
If tool execution fails for any reason, compute title case directly as a fallback.
Return only the transformed title-case text as plain text (no quotes, no markdown, no diagnostics).
""".strip()


@app.local_entrypoint()
def local(input_payload: dict[str, str]):
    text = str(input_payload.get("text") or input_payload.get("message") or "").strip()
    if not text:
        return {"ok": False, "error": "Provide --input text='hello world'"}
    return title_case_decorator(text)

