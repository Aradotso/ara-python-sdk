from __future__ import annotations

import json
import importlib.util
import os
import pathlib
import subprocess
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPO_ROOT / "examples"
EXAMPLE_VARIANT_FILES = (
    "01-a-agent-skills-loading.py",
    "01-b-agent-skills-loading.py",
    "01-c-agent-skills-loading.py",
)


def _run_app_json(
    cwd: pathlib.Path,
    app_file: str,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: int = 180,
    retries: int = 0,
) -> dict:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    if not args:
        raise AssertionError("Expected at least one CLI command argument")
    command = [sys.executable, "-m", "ara_sdk", args[0], app_file, *args[1:]]
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


def _load_example_module(name: str, file_name: str):
    path = EXAMPLES_ROOT / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Failed to load module spec: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_examples_do_not_inject_fake_api_keys() -> None:
    # Regression guard: examples must not silently inject fake auth keys.
    for file_name in EXAMPLE_VARIANT_FILES:
        source = (EXAMPLES_ROOT / file_name).read_text(encoding="utf-8")
        assert "local-demo-key" not in source
        assert 'sys.argv[1] == "local"' not in source


def test_example_manifests_smoke() -> None:
    # Validate example wiring without any local execution hook.
    inline_mod = _load_example_module("skill_inline_example", "01-a-agent-skills-loading.py")
    inline_manifest = inline_mod.app.manifest
    inline_agents = inline_manifest["agent"]["agents"]
    assert inline_agents[0]["id"] == "title-case-inline-instructions-agent"
    assert "prompt_factory" in inline_agents[0]

    script_mod = _load_example_module("skill_script_example", "01-b-agent-skills-loading.py")
    script_manifest = script_mod.app.manifest
    runtime_files = script_manifest["runtime_profile"]["files"]
    assert isinstance(runtime_files, list) and runtime_files
    assert runtime_files[0]["path"] == "scripts/title_case.py"

    decorator_mod = _load_example_module("skill_decorator_example", "01-c-agent-skills-loading.py")
    decorator_manifest = decorator_mod.app.manifest
    tools = decorator_manifest["agent"]["tools"]
    assert tools[0]["function"]["name"] == "title_case_decorator"


@pytest.mark.skipif(
    os.getenv("RUN_SKILL_LOADING_E2E", "").strip() not in ("1", "true", "yes"),
    reason="Set RUN_SKILL_LOADING_E2E=1 to run live deploy/run reliability checks.",
)
def test_live_reliability_probe_three_of_three() -> None:
    if not os.getenv("ARA_API_KEY"):
        pytest.skip("ARA_API_KEY is required for live deploy/run checks.")

    cases = [
        {
            "file": "01-a-agent-skills-loading.py",
            "agent": "title-case-inline-instructions-agent",
            "probe_expected": "PROBE:inline-ok:Hello From Ara Sdk",
            "normal_expected": "Hello From Ara Sdk",
        },
        {
            "file": "01-b-agent-skills-loading.py",
            "agent": "title-case-runtime-file-agent",
            "probe_expected": "PROBE:script-ok:Hello From Ara Sdk",
            "normal_expected": "Hello From Ara Sdk",
        },
        {
            "file": "01-c-agent-skills-loading.py",
            "agent": "title-case-decorator-agent",
            "probe_expected": "PROBE:decorator-ok:Hello From Ara Sdk",
            "normal_expected": "Hello From Ara Sdk",
        },
    ]

    for case in cases:
        deploy_payload = _run_app_json(EXAMPLES_ROOT, case["file"], "deploy", timeout=240, retries=2)
        runtime_key = str(deploy_payload.get("runtime_key") or "").strip()
        if not runtime_key:
            raise AssertionError(f"deploy did not return runtime_key for {case['file']}")

        probe_outputs: list[str] = []
        for _attempt in range(3):
            run_payload = _run_app_json(
                EXAMPLES_ROOT,
                case["file"],
                "run",
                "--agent",
                case["agent"],
                "--message",
                "RELIABILITY_PROBE|hello from ara sdk",
                env={
                    "ARA_SDK_DEBUG_HTTP_ERRORS": "true",
                    "ARA_RUNTIME_KEY": runtime_key,
                },
                timeout=240,
                retries=2,
            )
            probe_outputs.append(_extract_output_text(run_payload))
        assert probe_outputs == [case["probe_expected"]] * 3

        normal_payload = _run_app_json(
            EXAMPLES_ROOT,
            case["file"],
            "run",
            "--agent",
            case["agent"],
            "--message",
            "hello from ara sdk",
            env={
                "ARA_SDK_DEBUG_HTTP_ERRORS": "true",
                "ARA_RUNTIME_KEY": runtime_key,
            },
            timeout=240,
            retries=2,
        )
        assert _extract_output_text(normal_payload) == case["normal_expected"]
