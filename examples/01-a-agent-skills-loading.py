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
    "Ara Skill Pattern 01 (Inline Instructions)",
    project_name="skill-inline-v1",
    description=(
        "Minimal Ara SDK app with no custom tool code. "
        "Agent behavior is defined entirely by inline execution instructions in task text."
    ),
)


@app.agent(
    id="title-case-inline-instructions-agent",
    entrypoint=True,
    prompt_factory=True,
    skills=["bash"],
)
def title_case_agent(payload: dict) -> str:
    """Build runtime instructions from JSON input payload."""
    input_payload = payload if isinstance(payload, dict) else {}
    text = str(input_payload.get("text") or input_payload.get("message") or "").strip()
    mode = str(input_payload.get("mode") or "").strip().lower()
    probe_requested = mode == "probe" or text.startswith("RELIABILITY_PROBE|")
    if probe_requested:
        return """
You are an instruction-only title-case assistant.
Use bash + python argv mode only.
Reliability probe mode: if input starts with 'RELIABILITY_PROBE|', take the text after '|'.
Execute python with argv (never heredoc interpolation).
Return exactly:
PROBE:inline-ok:<Title Case Text>
with no extra words.
""".strip()
    return """
You are an instruction-only title-case assistant.
This app intentionally defines no custom title-case tool function.
When asked to transform text, use bash and execute python with the user text passed as argv
(never interpolated into a heredoc/body).

Use this pattern:
python3 -c "import sys; t=sys.argv[1]; print(' '.join(w[:1].upper()+w[1:].lower() for w in t.split()))" -- <shell-quoted-user-text>

Return only the transformed title-case text and no diagnostics.
""".strip()


