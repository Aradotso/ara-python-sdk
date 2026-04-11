from __future__ import annotations

import pathlib
import shlex
import sys

for parent in pathlib.Path(__file__).resolve().parents:
    src_dir = parent / "src"
    if (src_dir / "ara_sdk").exists():
        sys.path.insert(0, str(src_dir))
        break

from ara_sdk import App

AGENT_ID = "title-case-inline-instructions-agent"

ARGV_COMMAND_TEMPLATE = (
    "python3 -c "
    "\"import sys; t=sys.argv[1]; print(' '.join(w[:1].upper()+w[1:].lower() for w in t.split()))\" "
    "-- {quoted_text}"
)

app = App(
    "Ara Skill Pattern 01 (Inline Instructions)",
    project_name="skill-inline-v1",
    description=(
        "Minimal Ara SDK app with no custom tool code. "
        "Agent behavior is defined entirely by inline execution instructions in task text."
    ),
)


@app.agent(
    id=AGENT_ID,
    entrypoint=True,
    task=(
        "You are an instruction-only title-case assistant. "
        "This app intentionally defines no custom title-case tool function. "
        "When asked to transform text, use bash and execute python with the user text passed "
        "as argv (never interpolated into a heredoc/body).\n\n"
        "Use this pattern:\n"
        "python3 -c \"import sys; t=sys.argv[1]; print(' '.join(w[:1].upper()+w[1:].lower() "
        "for w in t.split()))\" -- <shell-quoted-user-text>\n\n"
        "Default output: return only the transformed title-case text and no diagnostics.\n\n"
        "Reliability probe mode (highest priority): if the user message starts with "
        "'RELIABILITY_PROBE|', take the text after '|' as input and return exactly:\n"
        "PROBE:inline-ok:<Title Case Text>\n"
        "with no extra words."
    ),
    skills=["bash"],
)
def title_case_agent():
    """Instruction-only agent with inline command guidance."""


@app.local_entrypoint()
def local(input_payload: dict[str, str]):
    text = str(input_payload.get("text") or input_payload.get("message") or "").strip()
    resolved_text = text or "hello world"
    return {
        "ok": True,
        "mode": "inline-instructions-only",
        "notes": "No title-case tool is defined in this app; behavior lives in task text.",
        "agent_id": AGENT_ID,
        "input_text": resolved_text,
        "command_to_run": ARGV_COMMAND_TEMPLATE.format(quoted_text=shlex.quote(resolved_text)),
    }

