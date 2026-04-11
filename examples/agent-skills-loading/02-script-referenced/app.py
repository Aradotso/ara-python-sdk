from __future__ import annotations

import pathlib
import shlex
import sys

for parent in pathlib.Path(__file__).resolve().parents:
    src_dir = parent / "src"
    if (src_dir / "ara_sdk").exists():
        sys.path.insert(0, str(src_dir))
        break

from ara_sdk import App, local_file, runtime

AGENT_ID = "title-case-runtime-file-agent"
PROJECT_NAME = "skill-script-v1"
ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT_SOURCE_FILE = ROOT / "scripts" / "title_case.py"
SCRIPT_PATH = "scripts/title_case.py"

app = App(
    "Ara Skill Pattern 02 (Script Referenced)",
    project_name=PROJECT_NAME,
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
    id=AGENT_ID,
    entrypoint=True,
    # Path discovery is required because app files are mounted under app-id scoped runtime roots.
    task=(
        "You are a file-backed title-case assistant. "
        "A Python script is preloaded via runtime files at path 'scripts/title_case.py'. "
        "Resolve the runtime file path with:\n\n"
        "SCRIPT_PATH=\"$(python3 - <<'PY'\n"
        "import glob\n"
        f"paths = sorted(glob.glob('/root/.ara/workspace/.apps/*{PROJECT_NAME}*/scripts/title_case.py'))\n"
        "print(paths[0] if paths else '')\n"
        "PY\n"
        ")\"\n\n"
        "Then execute:\n\n"
        "python3 \"$SCRIPT_PATH\" --text \"INPUT_TEXT\"\n\n"
        "Default behavior: if the script command fails, immediately run this fallback:\n\n"
        "python3 - <<'PY'\n"
        "text = \"INPUT_TEXT\"\n"
        "print(\" \".join(w[:1].upper() + w[1:].lower() for w in text.split()))\n"
        "PY\n\n"
        "If script execution is unavailable, still compute title case manually. "
        "Do not apologize and do not describe failures. "
        "In all cases, return only the transformed title-case text and no diagnostics.\n\n"
        "Reliability probe mode (highest priority): if the user message starts with "
        "'RELIABILITY_PROBE|', you MUST take the text after '|' as input and run the uploaded "
        "script path flow above (no fallback). "
        "If script execution succeeds, return exactly:\n"
        "PROBE:script-ok:<Title Case Text>\n"
        "If script execution fails, return exactly:\n"
        "PROBE:script-fail"
    ),
    skills=["bash"],
)
def title_case_agent():
    """Agent that runs the uploaded runtime script by path."""


@app.local_entrypoint()
def local(input_payload: dict[str, str]):
    text = str(input_payload.get("text") or input_payload.get("message") or "").strip()
    resolved_text = text or "hello world"
    return {
        "ok": True,
        "mode": "runtime-file-upload-reference",
        "agent_id": AGENT_ID,
        "input_text": resolved_text,
        "uploaded_script_path": SCRIPT_PATH,
        "source_file": str(SCRIPT_SOURCE_FILE),
        "command_to_run": (
            f'python3 /root/.ara/workspace/.apps/$APP_ID/{SCRIPT_PATH} '
            f"--text {shlex.quote(resolved_text)}"
        ),
        "runtime_files": app.manifest.get("runtime_profile", {}).get("files", []),
    }

