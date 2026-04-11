from __future__ import annotations

import pytest

from ara_sdk import core as sdk_core


def test_runtime_cli_formats_missing_api_key_error(monkeypatch, tmp_path):
    class _FailingRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            raise RuntimeError("Missing required env var: ARA_API_KEY.")

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _FailingRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    with pytest.raises(SystemExit, match=r"ara runtime: Missing required env var: ARA_API_KEY\."):
        sdk_core.run_runtime_cli(["capabilities", "--session", "sess-123"])


def test_runtime_cli_tools_execute_requires_tool_with_clean_exit(monkeypatch, tmp_path):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            return cls()

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    with pytest.raises(SystemExit, match=r"ara runtime: tools execute requires --tool"):
        sdk_core.run_runtime_cli(["tools", "execute", "--session", "sess-123"])


def test_runtime_cli_control_call_requires_action_with_clean_exit(monkeypatch, tmp_path):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            return cls()

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    with pytest.raises(SystemExit, match=r"ara runtime: control call requires --action"):
        sdk_core.run_runtime_cli(["control", "call", "--session", "sess-123"])
