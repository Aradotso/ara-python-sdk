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


def test_runtime_cli_session_exec_requires_command(monkeypatch, tmp_path):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            return cls()

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    with pytest.raises(SystemExit, match=r"ara runtime: session exec requires --command"):
        sdk_core.run_runtime_cli(["session", "exec"])


def test_runtime_cli_session_lifecycle_commands(monkeypatch, tmp_path, capsys):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

        def session_start(self):
            return {"session_id": "sess-started"}

        def session_status(self):
            return {"sessionId": "sess-started"}

        def session_stop(self):
            return {"ok": True}

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    sdk_core.run_runtime_cli(["session", "start"])
    out = capsys.readouterr().out
    assert '"session_id": "sess-started"' in out

    sdk_core.run_runtime_cli(["session", "status"])
    out = capsys.readouterr().out
    assert '"sessionId": "sess-started"' in out

    sdk_core.run_runtime_cli(["session", "stop"])
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()


def test_runtime_cli_session_exec_command(monkeypatch, tmp_path, capsys):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

        def session_exec(self, *, command: str, timeout_seconds: int):
            return {"ok": True, "command": command, "timeout_seconds": timeout_seconds}

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    sdk_core.run_runtime_cli(
        ["session", "exec", "--command", "pwd", "--timeout-seconds", "45"]
    )
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()
    assert '"command": "pwd"' in out
    assert '"timeout_seconds": 45' in out


def test_runtime_cli_session_keepalive_runs_requested_iterations(monkeypatch, tmp_path, capsys):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

        def session_heartbeat(self):
            return {"ok": True}

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))
    monkeypatch.setattr(sdk_core.time, "sleep", lambda *_: None)

    sdk_core.run_runtime_cli(
        ["session", "keepalive", "--interval-seconds", "1", "--iterations", "2"]
    )
    out = capsys.readouterr().out
    assert out.count('"event": "heartbeat"') == 2
    assert '"status": "completed"' in out
    assert '"iterations": 2' in out


def test_runtime_cli_automation_remove_requires_yes(monkeypatch, tmp_path):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    with pytest.raises(SystemExit, match=r"ara runtime: automation remove requires --yes"):
        sdk_core.run_runtime_cli(["automation", "remove", "--id", "job_123"])


def test_runtime_cli_automation_purge_requires_all_and_yes(monkeypatch, tmp_path):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    with pytest.raises(SystemExit, match=r"ara runtime: automation purge requires --all"):
        sdk_core.run_runtime_cli(["automation", "purge", "--yes"])
    with pytest.raises(SystemExit, match=r"ara runtime: automation purge requires --yes"):
        sdk_core.run_runtime_cli(["automation", "purge", "--all"])


def test_runtime_cli_automation_add_command(monkeypatch, tmp_path, capsys):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

        def automation_create_job(self, **kwargs):  # noqa: ANN003
            return {"ok": True, "job": kwargs}

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    sdk_core.run_runtime_cli(
        [
            "automation",
            "add",
            "--name",
            "daily-check",
            "--cron",
            "0 9 * * *",
            "--message",
            "Daily summary",
            "--timezone",
            "UTC",
        ]
    )
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()
    assert '"name": "daily-check"' in out
    assert '"schedule_kind": "cron"' in out


def test_runtime_cli_automation_remove_uses_safe_delete(monkeypatch, tmp_path, capsys):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

        def automation_delete_job_safe(self, **kwargs):  # noqa: ANN003
            return {"ok": True, "action": "disabled_after_delete_failure", "job_id": kwargs.get("job_id")}

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    sdk_core.run_runtime_cli(["automation", "remove", "--id", "job_123", "--yes"])
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()
    assert '"disabled_after_delete_failure"' in out


def test_runtime_cli_automation_purge_command(monkeypatch, tmp_path, capsys):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

        def automation_purge_jobs(self, **kwargs):  # noqa: ANN003
            return {"ok": True, "initial_count": 3, "deleted_count": 2, "disabled_count": 1}

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    sdk_core.run_runtime_cli(["automation", "purge", "--all", "--yes"])
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()
    assert '"initial_count": 3' in out


def test_runtime_cli_automation_enable_disable_commands(monkeypatch, tmp_path, capsys):
    class _DummyRuntimeClient:
        @classmethod
        def from_env(cls, *, cwd=None):
            _ = cwd
            return cls()

        def automation_update_job(self, **kwargs):  # noqa: ANN003
            return {"ok": True, "job": kwargs}

    monkeypatch.setattr(sdk_core, "AraRuntimeClient", _DummyRuntimeClient)
    monkeypatch.setattr(sdk_core.os, "getcwd", lambda: str(tmp_path))

    sdk_core.run_runtime_cli(["automation", "enable", "--id", "job_123"])
    out = capsys.readouterr().out
    assert '"enabled": true' in out.lower()

    sdk_core.run_runtime_cli(["automation", "disable", "--id", "job_123"])
    out = capsys.readouterr().out
    assert '"enabled": false' in out.lower()


def test_runtime_client_session_start_uses_extended_timeout():
    captured: dict[str, object] = {}

    class _DummyHttp:
        def _request(self, path: str, **kwargs):  # noqa: ANN001
            captured["path"] = path
            captured.update(kwargs)
            return {"ok": True}

    client = sdk_core.AraRuntimeClient.__new__(sdk_core.AraRuntimeClient)
    client.http = _DummyHttp()

    response = sdk_core.AraRuntimeClient.session_start(client)

    assert response["ok"] is True
    assert captured["path"] == "/session/start"
    assert captured["timeout_seconds"] == 120


def test_runtime_client_session_heartbeat_uses_heartbeat_endpoint():
    captured: dict[str, object] = {}

    class _DummyHttp:
        def _request(self, path: str, **kwargs):  # noqa: ANN001
            captured["path"] = path
            captured.update(kwargs)
            return {"ok": True}

    client = sdk_core.AraRuntimeClient.__new__(sdk_core.AraRuntimeClient)
    client.http = _DummyHttp()

    response = sdk_core.AraRuntimeClient.session_heartbeat(client)

    assert response["ok"] is True
    assert captured["path"] == "/session/heartbeat"
    assert captured["method"] == "POST"
    assert captured["body"] == {}


def test_runtime_client_session_exec_uses_terminal_exec_endpoint():
    captured: dict[str, object] = {}

    class _DummyHttp:
        def _request(self, path: str, **kwargs):  # noqa: ANN001
            captured["path"] = path
            captured.update(kwargs)
            return {"ok": True}

    client = sdk_core.AraRuntimeClient.__new__(sdk_core.AraRuntimeClient)
    client.http = _DummyHttp()

    response = sdk_core.AraRuntimeClient.session_exec(
        client,
        command="echo hi",
        timeout_seconds=75,
    )

    assert response["ok"] is True
    assert captured["path"] == "/session/terminal/exec"
    assert captured["method"] == "POST"
    assert captured["body"] == {"command": "echo hi", "timeout_seconds": 75}
    assert captured["timeout_seconds"] == 105


def test_runtime_client_automation_create_job_uses_jobs_endpoint():
    captured: dict[str, object] = {}

    class _DummyHttp:
        def _request(self, path: str, **kwargs):  # noqa: ANN001
            captured["path"] = path
            captured.update(kwargs)
            return {"ok": True}

    client = sdk_core.AraRuntimeClient.__new__(sdk_core.AraRuntimeClient)
    client.http = _DummyHttp()

    response = sdk_core.AraRuntimeClient.automation_create_job(
        client,
        name="daily-check",
        schedule_kind="cron",
        schedule_expr="0 9 * * *",
        payload={"kind": "agent_turn", "message": "Daily summary"},
    )

    assert response["ok"] is True
    assert captured["path"] == "/automations/jobs"
    assert captured["method"] == "POST"
    assert isinstance(captured.get("body"), dict)
    assert captured["body"]["name"] == "daily-check"


def test_runtime_client_automation_update_delete_runs_replay_use_expected_endpoints():
    captured: list[tuple[str, dict[str, object]]] = []

    class _DummyHttp:
        def _request(self, path: str, **kwargs):  # noqa: ANN001
            captured.append((path, dict(kwargs)))
            return {"ok": True}

    client = sdk_core.AraRuntimeClient.__new__(sdk_core.AraRuntimeClient)
    client.http = _DummyHttp()

    sdk_core.AraRuntimeClient.automation_update_job(client, job_id="job_123", enabled=True)
    sdk_core.AraRuntimeClient.automation_delete_job(client, job_id="job_123")
    sdk_core.AraRuntimeClient.automation_list_runs(client, state="failed")
    sdk_core.AraRuntimeClient.automation_replay_run(client, run_id="run_123")

    assert captured[0][0] == "/automations/jobs/job_123"
    assert captured[0][1]["method"] == "PATCH"
    assert captured[1][0] == "/automations/jobs/job_123"
    assert captured[1][1]["method"] == "DELETE"
    assert captured[2][0] == "/automations/runs?state=failed"
    assert captured[2][1]["method"] == "GET"
    assert captured[3][0] == "/automations/runs/run_123/replay"
    assert captured[3][1]["method"] == "POST"


def test_runtime_client_automation_delete_job_safe_disables_on_failure():
    class _DummyClient:
        def __init__(self):
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def _request(self, path: str, **kwargs):  # noqa: ANN001
            method = str(kwargs.get("method") or "")
            body = dict(kwargs.get("body") or {})
            self.calls.append((path, method, body))
            if method == "DELETE":
                raise RuntimeError("DELETE failed (500)")
            return {"ok": True}

    client = sdk_core.AraRuntimeClient.__new__(sdk_core.AraRuntimeClient)
    client.http = _DummyClient()

    result = sdk_core.AraRuntimeClient.automation_delete_job_safe(
        client,
        job_id="job_123",
        disable_on_failure=True,
    )

    assert result["ok"] is True
    assert result["action"] == "disabled_after_delete_failure"
    assert result["job_id"] == "job_123"
