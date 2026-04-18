import importlib.util
import io
import urllib.error

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


def test_from_env_uses_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ARA_API_BASE_URL", "https://api.ara.so")
    monkeypatch.setenv("ARA_API_KEY", "ara_api_key_primary_0123456789abcdef")

    client = core.AraClient.from_env(manifest=_manifest_with_runtime(runtime_profile={}), cwd=str(tmp_path))
    assert client.http.api_key == "ara_api_key_primary_0123456789abcdef"


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


def test_app_cli_rejects_removed_legacy_commands():
    manifest = _manifest_with_runtime(runtime_profile={})
    for command in ("events", "run-async", "run-status", "setup", "setup-auth", "invite"):
        with pytest.raises(SystemExit):
            core._run_app_cli(manifest, argv=[command])


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
