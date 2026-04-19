import importlib.util
import io
import json
import ssl
import stat
import threading
import time
import urllib.error
from typing import Any

import pytest

import ara_sdk
from ara_sdk import core


def _manifest_with_runtime(runtime_profile: dict) -> dict:
    return {
        "name": "Test App",
        "slug": "test-app",
        "description": "",
        "agent": {},
        "workflows": [],
        "interfaces": {},
        "runtime_profile": runtime_profile,
    }


def test_minimal_automation_declaration_builds_manifest_without_app_variable():
    core._pop_minimal_app()

    @core.tool
    def send_email(to: str, subject: str, body: str) -> dict:
        _ = to, subject, body
        sender = core.secret("CRON_EMAIL_FROM")
        return {"ok": True, "from": sender}

    core.Automation(
        "weekday-priority-agent",
        system_instructions="Send weekday priority digest.",
        tools=[send_email],
    )

    app = core._pop_minimal_app()
    assert app is not None
    manifest = app.manifest
    assert manifest["slug"] == "weekday-priority-agent"
    assert manifest["agent"]["agents"][0]["id"] == "weekday-priority-agent"
    assert manifest["agent"]["agents"][0]["skills"] == ["send_email"]
    assert manifest["agent"]["tools"][0]["required_env"] == ["CRON_EMAIL_FROM"]
    assert manifest["interfaces"]["inherit_owner_tools"] is True


def test_automation_connector_skills_compile_to_tool_privileges():
    core._pop_minimal_app()

    @core.tool
    def summarize() -> dict:
        return {"ok": True}

    core.Automation(
        "calendar-helper",
        system_instructions="Handle calendar requests.",
        tools=[summarize],
        skills=[
            core.connectors.google_calendar.list_events,
            core.connectors.google_calendar.create_event,
        ],
    )

    app = core._pop_minimal_app()
    assert app is not None
    manifest = app.manifest
    assert manifest["interfaces"]["inherit_owner_tools"] is True
    assert manifest["interfaces"]["tool_privileges"] == [
        {
            "toolkit": "googlecalendar",
            "allowed_actions": ["create_event", "list_events"],
            "scopes": [],
        }
    ]
    assert manifest["agent"]["agents"][0]["skills"] == [
        "summarize",
        "connector:googlecalendar:list_events",
        "connector:googlecalendar:create_event",
    ]


def test_automation_connector_wildcard_overrides_specific_actions():
    core._pop_minimal_app()
    core.Automation(
        "calendar-wide",
        system_instructions="Handle calendar broadly.",
        skills=[
            core.connectors.google_calendar.list_events,
            core.connectors.google_calendar,
        ],
    )

    app = core._pop_minimal_app()
    assert app is not None
    manifest = app.manifest
    assert manifest["interfaces"]["tool_privileges"] == [
        {"toolkit": "googlecalendar", "allowed_actions": [], "scopes": []}
    ]


def test_automation_can_disable_connectors_explicitly():
    core._pop_minimal_app()
    core.Automation(
        "connectors-off",
        system_instructions="No connector usage.",
        allow_connector_tools=False,
    )

    app = core._pop_minimal_app()
    assert app is not None
    manifest = app.manifest
    assert manifest["interfaces"]["inherit_owner_tools"] is False


def test_explicit_connector_skills_override_allow_connector_tools_false():
    core._pop_minimal_app()
    core.Automation(
        "connectors-scoped",
        system_instructions="Use only Gmail send email.",
        allow_connector_tools=False,
        skills=[core.connectors.gmail.send_email],
    )

    app = core._pop_minimal_app()
    assert app is not None
    manifest = app.manifest
    assert manifest["interfaces"]["inherit_owner_tools"] is True
    assert manifest["interfaces"]["tool_privileges"] == [
        {"toolkit": "gmail", "allowed_actions": ["send_email"], "scopes": []}
    ]


def test_from_env_uses_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ARA_API_BASE_URL", "https://api.ara.so")
    monkeypatch.setenv("ARA_API_KEY", "ara_api_key_primary_0123456789abcdef")

    client = core.AraClient.from_env(manifest=_manifest_with_runtime(runtime_profile={}), cwd=str(tmp_path))
    assert client.http.api_key == "ara_api_key_primary_0123456789abcdef"


def test_runtime_key_resolves_from_cache_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )
    core._save_local_runtime_key(tmp_path, slug="test-app", runtime_key="ak_app_cached_local")

    assert client._resolve_runtime_key() == "ak_app_cached_local"


def test_runtime_key_resolves_from_legacy_project_cache_when_home_cache_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )
    (tmp_path / core.CLI_RUNTIME_KEYS_FILENAME).write_text(
        json.dumps({"test-app": "ak_app_legacy_123"}),
        encoding="utf-8",
    )

    assert client._resolve_runtime_key() == "ak_app_legacy_123"


def test_runtime_key_cache_written_with_secure_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    core._save_local_runtime_key(tmp_path, slug="test-app", runtime_key="ak_app_secure")
    path = tmp_path / ".ara" / core.CLI_RUNTIME_KEYS_FILENAME
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_merge_connector_tool_privileges_rejects_invalid_explicit_actions():
    with pytest.raises(ValueError, match="allowed_actions contained no valid"):
        core._merge_connector_tool_privileges(
            [{"toolkit": "gmail", "allowed_actions": ["!!!"]}],
            [],
        )


def test_from_env_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ARA_API_BASE_URL", "https://api.ara.so")
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.setattr(core, "_resolve_control_plane_bearer", lambda: "")
    with pytest.raises(RuntimeError, match=r"No credentials found\. Set ARA_API_KEY or run `ara auth login`\.$"):
        core.AraClient.from_env(manifest=_manifest_with_runtime(runtime_profile={}), cwd=str(tmp_path))


def test_http_error_redacts_response_body_by_default(monkeypatch):
    leaked = "internal stack trace: host=prod-worker-17"

    def _raise_http_error(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://api.ara.so/apps",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(leaked.encode("utf-8")),
        )

    monkeypatch.delenv("ARA_SDK_DEBUG_HTTP_ERRORS", raising=False)
    monkeypatch.setattr(core.urllib.request, "urlopen", _raise_http_error)

    http = core._Http(base_url="https://api.ara.so", api_key="test-token")
    with pytest.raises(RuntimeError) as exc:
        http.list_apps()

    message = str(exc.value)
    assert "GET /apps failed (500)." in message
    assert "Response body hidden by default" in message
    assert leaked not in message


def test_http_error_includes_response_body_in_debug_mode(monkeypatch):
    details = '{"error":"upstream timeout"}'

    def _raise_http_error(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://api.ara.so/apps",
            code=504,
            msg="Gateway Timeout",
            hdrs=None,
            fp=io.BytesIO(details.encode("utf-8")),
        )

    monkeypatch.setenv("ARA_SDK_DEBUG_HTTP_ERRORS", "true")
    monkeypatch.setattr(core.urllib.request, "urlopen", _raise_http_error)

    http = core._Http(base_url="https://api.ara.so", api_key="test-token")
    with pytest.raises(RuntimeError) as exc:
        http.list_apps()

    message = str(exc.value)
    assert "GET /apps failed (504):" in message
    assert details in message


def test_http_retries_ssl_cert_verify_fail_with_certifi_bundle(monkeypatch):
    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"ok": true}'

    calls: list[dict[str, Any]] = []
    certifi_context = object()
    ssl_error = ssl.SSLCertVerificationError(1, "certificate verify failed")

    def _urlopen(req, timeout=None, context=None):
        _ = req
        calls.append({"timeout": timeout, "context": context})
        if len(calls) == 1:
            raise urllib.error.URLError(ssl_error)
        return _FakeResponse()

    monkeypatch.setattr(core, "_certifi_ssl_context", lambda: certifi_context)
    monkeypatch.setattr(core.urllib.request, "urlopen", _urlopen)

    http = core._Http(base_url="https://api.ara.so", api_key="test-token")
    out = http.list_apps()

    assert out == {"ok": True}
    assert len(calls) == 2
    assert calls[0]["context"] is None
    assert calls[1]["context"] is certifi_context


def test_http_ssl_cert_verify_fail_without_fallback_includes_actionable_hint(monkeypatch):
    ssl_error = ssl.SSLCertVerificationError(1, "certificate verify failed")

    def _raise_ssl(*args, **kwargs):
        _ = (args, kwargs)
        raise urllib.error.URLError(ssl_error)

    monkeypatch.setattr(core, "_certifi_ssl_context", lambda: None)
    monkeypatch.setattr(core.urllib.request, "urlopen", _raise_ssl)

    http = core._Http(base_url="https://api.ara.so", api_key="test-token")
    with pytest.raises(RuntimeError) as exc:
        http.list_apps()

    message = str(exc.value)
    assert "TLS certificate verification failed." in message
    assert "Install or upgrade certifi" in message


def test_http_ssl_cert_verify_fail_after_certifi_retry_includes_retry_hint(monkeypatch):
    ssl_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    certifi_context = object()
    calls: list[dict[str, Any]] = []

    def _raise_ssl(req, timeout=None, context=None):
        _ = req
        calls.append({"timeout": timeout, "context": context})
        raise urllib.error.URLError(ssl_error)

    monkeypatch.setattr(core, "_certifi_ssl_context", lambda: certifi_context)
    monkeypatch.setattr(core.urllib.request, "urlopen", _raise_ssl)

    http = core._Http(base_url="https://api.ara.so", api_key="test-token")
    with pytest.raises(RuntimeError) as exc:
        http.list_apps()

    message = str(exc.value)
    assert "TLS certificate verification failed." in message
    assert "Retried with certifi CA bundle, but TLS verification still failed." in message
    assert len(calls) == 2
    assert calls[0]["context"] is None
    assert calls[1]["context"] is certifi_context


def test_app_cli_rejects_removed_legacy_commands():
    manifest = _manifest_with_runtime(runtime_profile={})
    for command in ("events", "run-async", "run-status", "setup", "setup-auth", "invite"):
        with pytest.raises(SystemExit):
            core._run_app_cli(manifest, argv=[command])


def test_app_cli_run_stream_logs_follows_current_run(monkeypatch, capsys, tmp_path):
    manifest = _manifest_with_runtime(runtime_profile={})
    captured: dict[str, Any] = {}
    fixed_run_id = "run_test_stream_123"
    log_path = tmp_path / "run-stream.log"

    class _FakeClient:
        def run(self, *, agent_id, input_payload, runtime_key=None, app_header_key=None):
            captured["agent_id"] = agent_id
            captured["input_payload"] = dict(input_payload)
            captured["runtime_key"] = runtime_key
            captured["app_header_key"] = app_header_key
            return {"ok": True, "run_id": fixed_run_id}

        def logs(self, *, runtime_key=None, app_header_key=None):
            _ = runtime_key, app_header_key
            yield {
                "timestamp": "2026-04-18T00:00:00Z",
                "level": "info",
                "run_id": "run_other",
                "event_type": "run.started",
                "message": "Other run started",
            }
            yield {
                "timestamp": "2026-04-18T00:00:01Z",
                "level": "info",
                "run_id": fixed_run_id,
                "event_type": "run.started",
                "message": "Run started",
            }
            yield {
                "timestamp": "2026-04-18T00:00:02Z",
                "level": "info",
                "run_id": fixed_run_id,
                "event_type": "run.completed",
                "message": "Run completed",
            }

    monkeypatch.setattr(core.AraClient, "from_env", classmethod(lambda cls, *, manifest, cwd=None: _FakeClient()))
    monkeypatch.setattr(core, "_new_run_id", lambda: fixed_run_id)

    core._run_app_cli(
        manifest,
        argv=[
            "run",
            "--runtime-key",
            "ak_app_test",
            "--log-file",
            str(log_path),
            "--stream-logs-timeout-seconds",
            "1",
        ],
    )

    output = capsys.readouterr().out
    assert "run=run_test_stream_123 event=run.started Run started" in output
    assert "run=run_test_stream_123 event=run.completed Run completed" in output
    assert "run=run_other" not in output
    assert '"ok": true' in output
    assert captured["agent_id"] is None
    assert captured["runtime_key"] == "ak_app_test"
    assert captured["input_payload"]["run_id"] == fixed_run_id
    assert "idempotency_key" in captured["input_payload"]
    log_contents = log_path.read_text(encoding="utf-8")
    assert "run=run_test_stream_123 event=run.started Run started" in log_contents
    assert "run=run_test_stream_123 event=run.completed Run completed" in log_contents


def test_app_cli_run_no_stream_logs_skips_log_tail(monkeypatch, capsys):
    manifest = _manifest_with_runtime(runtime_profile={})

    class _FakeClient:
        def run(self, *, agent_id, input_payload, runtime_key=None, app_header_key=None):
            _ = agent_id, input_payload, runtime_key, app_header_key
            return {"ok": True, "run_id": "run_no_stream_1"}

        def logs(self, *, runtime_key=None, app_header_key=None):
            _ = runtime_key, app_header_key
            raise AssertionError("logs() should not be called when --no-stream-logs is set")

    monkeypatch.setattr(core.AraClient, "from_env", classmethod(lambda cls, *, manifest, cwd=None: _FakeClient()))

    core._run_app_cli(
        manifest,
        argv=[
            "run",
            "--runtime-key",
            "ak_app_test",
            "--no-stream-logs",
        ],
    )

    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert "run=" not in output


def test_run_auto_provisions_and_caches_runtime_key(monkeypatch, tmp_path):
    manifest = _manifest_with_runtime(runtime_profile={})
    calls: dict[str, Any] = {"create_key_count": 0}

    class _FakeHttp:
        def list_apps(self):
            return {"apps": [{"id": "app_test_1", "slug": "test-app", "role": "owner"}]}

        def create_key(self, app_id, *, name, requests_per_minute):
            calls["create_key_count"] += 1
            calls["create_key_app_id"] = app_id
            calls["create_key_name"] = name
            calls["create_key_rpm"] = requests_per_minute
            return {"key": "ak_app_auto_123"}

        def run_app(self, app_id, *, runtime_key=None, app_header_key=None, agent_id=None, input_payload=None, warmup=False):
            calls["run_app"] = {
                "app_id": app_id,
                "runtime_key": runtime_key,
                "app_header_key": app_header_key,
                "agent_id": agent_id,
                "input_payload": dict(input_payload or {}),
                "warmup": warmup,
            }
            return {"ok": True, "run_id": "run_auto_1"}

    monkeypatch.delenv("ARA_RUNTIME_KEY", raising=False)
    monkeypatch.delenv("ARA_APP_HEADER_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    client = core.AraClient(
        manifest=manifest,
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )
    client.http = _FakeHttp()

    out_first = client.run(agent_id=None, input_payload={"trigger": "first"})
    out_second = client.run(agent_id=None, input_payload={"trigger": "second"})

    assert out_first["ok"] is True
    assert out_second["ok"] is True
    assert calls["create_key_count"] == 1
    assert calls["run_app"]["runtime_key"] == "ak_app_auto_123"
    assert calls["run_app"]["app_header_key"] == ""
    runtime_key_cache = json.loads((tmp_path / ".ara" / ".runtime-keys.local").read_text(encoding="utf-8"))
    assert runtime_key_cache["test-app"] == "ak_app_auto_123"


def test_runtime_key_auto_provision_is_locked_across_concurrent_calls(monkeypatch, tmp_path):
    manifest = _manifest_with_runtime(runtime_profile={})
    monkeypatch.delenv("ARA_RUNTIME_KEY", raising=False)
    monkeypatch.delenv("ARA_APP_HEADER_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    calls: dict[str, Any] = {"create_key_count": 0}
    call_lock = threading.Lock()

    class _FakeHttp:
        def create_key(self, app_id, *, name, requests_per_minute):
            _ = app_id, name, requests_per_minute
            with call_lock:
                calls["create_key_count"] += 1
            time.sleep(0.05)
            return {"key": "ak_app_auto_lock_123"}

    client = core.AraClient(
        manifest=manifest,
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )
    client.http = _FakeHttp()

    barrier = threading.Barrier(2)
    results: list[tuple[str, str]] = []
    errors: list[Exception] = []

    def _worker():
        try:
            barrier.wait()
            results.append(client._ensure_runtime_credentials(app_id="app_test_1"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert not errors
    assert len(results) == 2
    assert calls["create_key_count"] == 1
    assert results[0][0] == "ak_app_auto_lock_123"
    assert results[1][0] == "ak_app_auto_lock_123"
    runtime_key_cache = json.loads((tmp_path / ".ara" / ".runtime-keys.local").read_text(encoding="utf-8"))
    assert runtime_key_cache["test-app"] == "ak_app_auto_lock_123"


def test_deploy_cli_attaches_cron_schedule_to_entrypoint_agent(monkeypatch, tmp_path):
    manifest = _manifest_with_runtime(runtime_profile={})
    manifest["agent"] = {
        "agents": [
            {"id": "weekday-priority-agent", "entrypoint": True, "skills": []},
        ]
    }
    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, manifest_arg):
            self._manifest = manifest_arg

        def deploy(self, **kwargs):
            captured["manifest"] = self._manifest
            captured["kwargs"] = kwargs
            return {"runtime_key_created": False, "runtime_key": "", "warmup": None}

    def _fake_from_env(cls, *, manifest, cwd):
        _ = cls, cwd
        return _FakeClient(manifest)

    monkeypatch.setattr(core.AraClient, "from_env", classmethod(_fake_from_env))
    monkeypatch.chdir(tmp_path)

    core._run_app_cli(manifest, argv=["deploy", "--cron", "*/5 * * * *"])

    resolved_manifest = captured["manifest"]
    assert isinstance(resolved_manifest, dict)
    agents = resolved_manifest.get("agent", {}).get("agents", [])
    assert isinstance(agents, list) and agents
    schedule = agents[0]["schedules"][0]
    assert schedule["id"] == "cli-managed-schedule"
    assert schedule["kind"] == "cron"
    assert schedule["cron"] == "*/5 * * * *"
    assert schedule["timezone"] == "UTC"
    assert schedule["run"]["type"] == "agent"
    assert schedule["run"]["agent_id"] == "weekday-priority-agent"


def test_deploy_cli_rejects_mixed_cron_and_every_flags():
    manifest = _manifest_with_runtime(runtime_profile={})
    manifest["agent"] = {"agents": [{"id": "agent-a", "entrypoint": True, "skills": []}]}
    with pytest.raises(SystemExit, match="Cannot combine --cron and --every-seconds"):
        core._run_app_cli(
            manifest,
            argv=["deploy", "--cron", "*/5 * * * *", "--every-seconds", "300"],
        )


def test_deploy_cli_log_flag_tails_logs_and_writes_file(monkeypatch, capsys, tmp_path):
    manifest = _manifest_with_runtime(runtime_profile={})
    captured: dict[str, Any] = {}
    log_path = tmp_path / "deploy-tail.log"

    class _FakeClient:
        def deploy(self, **kwargs):
            captured["deploy_kwargs"] = kwargs
            return {"runtime_key_created": True, "runtime_key": "ak_app_deploy_test", "warmup": None}

        def logs(self, *, runtime_key=None, app_header_key=None):
            captured["logs_runtime_key"] = runtime_key
            captured["logs_app_header_key"] = app_header_key
            yield {
                "timestamp": "2026-04-18T00:00:00Z",
                "level": "info",
                "run_id": "run_cron_1",
                "event_type": "run.started",
                "message": "Cron run started",
            }
            yield {
                "timestamp": "2026-04-18T00:00:01Z",
                "level": "info",
                "run_id": "run_cron_1",
                "event_type": "run.completed",
                "message": "Cron run completed",
            }

    monkeypatch.setattr(core.AraClient, "from_env", classmethod(lambda cls, *, manifest, cwd=None: _FakeClient()))

    core._run_app_cli(
        manifest,
        argv=[
            "deploy",
            "--log",
            "--log-file",
            str(log_path),
        ],
    )

    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert "run=run_cron_1 event=run.started Cron run started" in output
    assert "run=run_cron_1 event=run.completed Cron run completed" in output
    assert captured["logs_runtime_key"] is None
    assert captured["logs_app_header_key"] is None
    log_contents = log_path.read_text(encoding="utf-8")
    assert "run=run_cron_1 event=run.started Cron run started" in log_contents
    assert "run=run_cron_1 event=run.completed Cron run completed" in log_contents


def test_top_level_module_does_not_expose_legacy_symbols():
    for symbol in (
        "App",
        "Secret",
        "fastapi_endpoint",
        "invoke",
        "schedule",
        "scheduler",
        "runtime",
        "sandbox",
        "entrypoint",
        "file",
        "local_file",
        "AraRuntimeClient",
    ):
        assert not hasattr(ara_sdk, symbol)
    assert hasattr(ara_sdk, "connectors")



def test_deploy_reconciles_when_secret_refs_are_added_during_plan(monkeypatch, tmp_path):
    class _FakeHttp:
        def list_apps(self):
            return {"apps": []}

        def create_app(self, body):
            _ = body
            return {"app": {"id": "app_test_1"}}

        def update_app(self, app_id, body):
            _ = app_id, body
            return {"ok": True}

        def create_key(self, app_id, *, name, requests_per_minute):
            _ = app_id, name, requests_per_minute
            return {"key": "ak_app_test"}

    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )
    client.http = _FakeHttp()

    def _fake_extract(self, runtime_profile):
        _ = self
        out = dict(runtime_profile)
        out["secret_refs"] = [{"name": "sdk-dotenv-abcd1234"}]
        return [], out

    captured: dict[str, bool] = {}

    def _fake_sync(self, app_id, definitions, *, reconcile_runtime_secrets):
        _ = self, app_id, definitions
        captured["reconcile_runtime_secrets"] = bool(reconcile_runtime_secrets)
        return {"synced": [], "referenced_only": []}

    monkeypatch.setattr(core.AraClient, "_extract_secret_sync_plan", _fake_extract)
    monkeypatch.setattr(core.AraClient, "_sync_secret_definitions", _fake_sync)

    out = client.deploy()
    assert out["app_id"] == "app_test_1"
    assert captured["reconcile_runtime_secrets"] is True


def test_internal_schedule_binding_uses_private_builder_instances():
    entries = [{"id": "daily-email", "kind": "cron", "cron": "0 9 * * *", "timezone": "UTC"}]
    bound = core._bind_schedule_entries_to_target(entries, target_kind="tool", target_id="send_email")
    assert bound[0]["run"]["type"] == "tool"
    assert bound[0]["run"]["tool_name"] == "send_email"


def test_minimal_singleton_resets_across_distinct_script_modules(tmp_path):
    def _load(path):
        spec = importlib.util.spec_from_file_location(path.stem, str(path))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    core._pop_minimal_app()

    script_one = tmp_path / "automation_one.py"
    script_one.write_text(
        "\n".join(
            [
                "import ara_sdk as ara",
                "@ara.tool",
                "def tool_one() -> dict:",
                "    return {'ok': True}",
                "ara.Automation('first-automation', tools=[tool_one])",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    script_two = tmp_path / "automation_two.py"
    script_two.write_text(
        "\n".join(
            [
                "import ara_sdk as ara",
                "@ara.tool",
                "def tool_two() -> dict:",
                "    return {'ok': True}",
                "ara.Automation('second-automation', tools=[tool_two])",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _load(script_one)
    _load(script_two)
    app = core._pop_minimal_app()
    assert app is not None
    manifest = app.manifest
    assert manifest["slug"] == "second-automation"
    tools = manifest["agent"]["tools"]
    assert [tool["function"]["name"] for tool in tools] == ["tool_two"]
