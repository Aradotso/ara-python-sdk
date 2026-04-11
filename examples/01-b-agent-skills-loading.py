from __future__ import annotations

import pathlib
import sys

for parent in pathlib.Path(__file__).resolve().parents:
    src_dir = parent / "src"
    if (src_dir / "ara_sdk").exists():
        sys.path.insert(0, str(src_dir))
        break

from ara_sdk import App, local_file, runtime

ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT_SOURCE_FILE = ROOT / "assets" / "01-b-agent-skills-loading-title_case.py"
SCRIPT_PATH = "scripts/title_case.py"

app = App(
    "Ara Skill Pattern 02 (Script Referenced)",
    project_name="skill-script-v1",
    description=(
        "Minimal Ara SDK app that uploads a script into runtime files "
        "and instructs the agent to execute it by path."
    ),
    runtime_profile=runtime(
        files=[
            local_file(
                SCRIPT_SOURCE_FILE,
                SCRIPT_PATH,
                executable=False,
            )
        ]
    ),
)


@app.agent(
    id="title-case-runtime-file-agent",
    entrypoint=True,
    # Path discovery is required because app files are mounted under app-id scoped runtime roots.
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
You are a file-backed title-case assistant.
A Python script is preloaded via runtime files at path 'scripts/title_case.py'.
Resolve the runtime file path with:

SCRIPT_PATH="$(python3 - <<'PY'
import glob
paths = sorted(glob.glob('/root/.ara/workspace/.apps/*skill-script-v1*/scripts/title_case.py'))
print(paths[0] if paths else '')
PY
)"

Then execute:

python3 "$SCRIPT_PATH" --text "INPUT_TEXT"

Reliability probe mode: no fallback.
If input starts with 'RELIABILITY_PROBE|', use only the text after '|' as INPUT_TEXT.
If script execution succeeds, return exactly:
PROBE:script-ok:<Title Case Text>
If script execution fails, return exactly:
PROBE:script-fail
""".strip()
    return """
You are a file-backed title-case assistant.
A Python script is preloaded via runtime files at path 'scripts/title_case.py'.
Resolve the runtime file path with:

SCRIPT_PATH="$(python3 - <<'PY'
import glob
paths = sorted(glob.glob('/root/.ara/workspace/.apps/*skill-script-v1*/scripts/title_case.py'))
print(paths[0] if paths else '')
PY
)"

Then execute:

python3 "$SCRIPT_PATH" --text "INPUT_TEXT"

If script execution fails, immediately run fallback:

python3 - <<'PY'
text = "INPUT_TEXT"
print(" ".join(w[:1].upper() + w[1:].lower() for w in text.split()))
PY

Return only the transformed title-case text and no diagnostics.
""".strip()


