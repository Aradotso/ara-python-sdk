from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPO_ROOT / "examples" / "agent-skills-loading"


def _run_app_json(
    cwd: pathlib.Path,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: int = 180,
    retries: int = 0,
) -> dict:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    command = [sys.executable, "app.py", *args]
    transient_markers = (
        "failed (500)",
        "failed (502)",
        "failed (503)",
        "HTTP Error 500",
        "HTTP Error 502",
        "HTTP Error 503",
    )

    for attempt in range(retries + 1):
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode == 0:
            stdout = completed.stdout.strip()
            try:
                return json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Expected JSON output, got:\n{stdout}") from exc

        stderr = completed.stderr or ""
        stdout = completed.stdout or ""
        combined = f"{stdout}\n{stderr}"
        is_transient = any(marker in combined for marker in transient_markers)
        if is_transient and attempt < retries:
            time.sleep(2)
            continue

        raise AssertionError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    raise AssertionError("Unreachable")


def _extract_output_text(run_payload: dict) -> str:
    result = run_payload.get("result")
    if not isinstance(result, dict):
        return ""
    value = result.get("output_text")
    return str(value or "").strip()


def test_local_examples_smoke() -> None:
    # 01 and 02 local mode intentionally return command/runtime metadata so developers
    # can inspect execution patterns before deploying live runs.
    inline = _run_app_json(
        EXAMPLES_ROOT / "01-inline-instructions",
        "local",
        "--input",
        "text=hello from ara sdk",
    )
    assert inline["ok"] is True
    assert inline["result"]["mode"] == "inline-instructions-only"
    assert "python3 -c " in inline["result"]["command_to_run"]
    assert " -- " in inline["result"]["command_to_run"]

    script = _run_app_json(
        EXAMPLES_ROOT / "02-script-referenced",
        "local",
        "--input",
        "text=hello from ara sdk",
    )
    assert script["ok"] is True
    assert script["result"]["mode"] == "runtime-file-upload-reference"
    assert script["result"]["uploaded_script_path"] == "scripts/title_case.py"
    runtime_files = script["result"]["runtime_files"]
    assert isinstance(runtime_files, list) and runtime_files
    assert runtime_files[0]["path"] == "scripts/title_case.py"

    decorator = _run_app_json(
        EXAMPLES_ROOT / "03-decorator-handler",
        "local",
        "--input",
        "text=hello from ara sdk",
    )
    assert decorator["ok"] is True
    assert decorator["result"]["method"] == "decorator-handler"
    assert decorator["result"]["result"] == "Hello From Ara Sdk"


@pytest.mark.skipif(
    os.getenv("RUN_SKILL_LOADING_E2E", "").strip() not in ("1", "true", "yes"),
    reason="Set RUN_SKILL_LOADING_E2E=1 to run live deploy/run reliability checks.",
)
def test_live_reliability_probe_three_of_three() -> None:
    if not os.getenv("ARA_API_KEY"):
        pytest.skip("ARA_API_KEY is required for live deploy/run checks.")

    cases = [
        {
            "folder": "01-inline-instructions",
            "agent": "title-case-inline-instructions-agent",
            "probe_expected": "PROBE:inline-ok:Hello From Ara Sdk",
            "normal_expected": "Hello From Ara Sdk",
        },
        {
            "folder": "02-script-referenced",
            "agent": "title-case-runtime-file-agent",
            "probe_expected": "PROBE:script-ok:Hello From Ara Sdk",
            "normal_expected": "Hello From Ara Sdk",
        },
        {
            "folder": "03-decorator-handler",
            "agent": "title-case-decorator-agent",
            "probe_expected": "PROBE:decorator-ok:Hello From Ara Sdk",
            "normal_expected": "Hello From Ara Sdk",
        },
    ]

    for case in cases:
        cwd = EXAMPLES_ROOT / case["folder"]
        _ = _run_app_json(cwd, "deploy", timeout=240, retries=2)

        # Use runtime key auth path from deploy for deterministic run checks.
        header_key_path = cwd / ".app-header-key.local"
        header_key_path.unlink(missing_ok=True)

        probe_outputs: list[str] = []
        for _attempt in range(3):
            run_payload = _run_app_json(
                cwd,
                "run",
                "--agent",
                case["agent"],
                "--message",
                "RELIABILITY_PROBE|hello from ara sdk",
                env={"ARA_SDK_DEBUG_HTTP_ERRORS": "true"},
                timeout=240,
                retries=2,
            )
            probe_outputs.append(_extract_output_text(run_payload))
        assert probe_outputs == [case["probe_expected"]] * 3

        normal_payload = _run_app_json(
            cwd,
            "run",
            "--agent",
            case["agent"],
            "--message",
            "hello from ara sdk",
            env={"ARA_SDK_DEBUG_HTTP_ERRORS": "true"},
            timeout=240,
            retries=2,
        )
        assert _extract_output_text(normal_payload) == case["normal_expected"]
