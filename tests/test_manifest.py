import io
import os
import urllib.error

import pytest

from ara_sdk import App, Secret, invoke, runtime, sandbox, schedule, scheduler
from ara_sdk import core


def test_app_manifest_project_name_slug_priority():
    app = App(name="Investor Booker", project_name="Team Internal App")
    assert app.slug == "team-internal-app"


def test_agent_registers_profile_and_workflow():
    app = App(name="Test App")

    @app.agent(
        id="booking-coordinator",
        task="Coordinate booking tasks.",
        entrypoint=True,
        skills=["send_email", "automation_create"],
        schedules=[
            schedule.cron(
                id="weekday-digest",
                expr="0 10 * * 1-5",
                timezone="UTC",
                run=invoke.agent("booking-coordinator", input={"message": "send digest"}),
            )
        ],
        runtime=runtime(memory_mb=1024),
        sandbox=sandbox(max_concurrency=3),
    )
    def booking():
        """Coordinate booking agent workflows."""

    manifest = app.manifest
    agents = manifest["agent"]["agents"]
    profiles = manifest["agent"]["profiles"]
    workflows = manifest["workflows"]
    subagents = manifest["agent"]["subagents"]

    assert agents[0]["id"] == "booking-coordinator"
    assert agents[0]["skills"] == ["send_email", "automation_create"]
    assert agents[0]["schedules"][0]["kind"] == "cron"
    assert profiles[0]["id"] == "booking-coordinator"
    assert workflows[0]["id"] == "booking-coordinator"
    assert workflows[0]["trigger"]["type"] == "api"
    assert workflows[1]["trigger"]["type"] == "cron"
    assert subagents[0]["sandbox"]["max_concurrency"] == 3


def test_agent_omits_skills_when_unspecified_and_strips_runtime_secret_defs():
    app = App(name="No Skills Agent App")
    agent_runtime = runtime(
        secrets=[Secret.from_dict("provider-local", {"OPENAI_API_KEY": "sk-test"})],
    )

    @app.agent(
        id="general-agent",
        task="Handle generic requests.",
        runtime=agent_runtime,
    )
    def general_agent():
        """General agent."""

    manifest = app.manifest
    agents = manifest["agent"]["agents"]
    profiles = manifest["agent"]["profiles"]
    subagents = manifest["agent"]["subagents"]

    assert "skills" not in agents[0]
    assert "skills" not in profiles[0]
    assert "__secret_definitions" not in agents[0]["runtime"]
    assert "__secret_definitions" not in subagents[0]["runtime"]


def test_tool_manifest_shape():
    app = App(name="Tooling App")

    def send_email(to: str, subject: str, body: str) -> dict:
        """Send an email payload."""
        return {"ok": True, "to": to, "subject": subject, "body": body}

    app.tool(id="send_email", description="Send email via tool.")(send_email)

    manifest = app.manifest
    tools = manifest["agent"]["tools"]

    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "send_email"
    assert tools[0]["function"]["description"] == "Send email via tool."
    assert tools[0]["function"]["parameters"]["properties"]["subject"]["type"] == "string"
    assert tools[0]["function_name"] == "send_email"
    assert tools[0]["source"].startswith("def send_email")


def test_tool_supports_multiline_decorator_arguments():
    app = App(name="Tooling App")

    @app.tool(
        id="send_email",
        description="Send an email payload.",
    )
    def send_email(to: str):
        return {"ok": True, "to": to}

    tools = app.manifest["agent"]["tools"]
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "send_email"
    assert tools[0]["source"].startswith("def send_email")


def test_schedule_and_scheduler_builders():
    job = schedule.every(
        id="heartbeat",
        seconds=3600,
        run=invoke.tool("send_email", args={"to": "sveinung@ara.so", "subject": "hi", "body": "hello"}),
    )
    payload = scheduler.create(job, app_id="app_demo_1")
    assert payload["tool"] == "automation_create"
    assert payload["args"]["execution_kind"] == "app_tool_call"
    assert payload["args"]["app_id"] == "app_demo_1"
    assert payload["args"]["tool_name"] == "send_email"


def test_schedule_rejects_legacy_agent_field():
    with pytest.raises(ValueError, match="invoke\\.agent\\(\\.\\.\\.\\) requires non-empty agent id"):
        schedule.cron(
            id="daily",
            expr="0 9 * * *",
            run={"type": "agent", "agent": "booking-coordinator"},
        )


def test_sandbox_allows_multisandbox_spawn_shape():
    cfg = sandbox(
        policy="dedicated",
        key="planner",
        allow_spawn=True,
        spawn_to=["researcher", "verifier"],
        max_spawn_depth=3,
        max_children_per_parent=4,
        max_total_child_sessions_per_run=9,
        ephemeral_ttl_minutes=5,
        child_policy="ephemeral",
        child_runtime=runtime(memory_mb=1024),
    )
    assert cfg["policy"] == "dedicated"
    assert cfg["key"] == "planner"
    assert cfg["spawn"]["allow"] is True
    assert cfg["spawn"]["to"] == ["researcher", "verifier"]
    assert cfg["spawn"]["max_depth"] == 3
    assert cfg["spawn"]["max_children_per_parent"] == 4
    assert cfg["spawn"]["max_total_child_sessions_per_run"] == 9
    assert cfg["spawn"]["ephemeral_ttl_minutes"] == 5
    assert cfg["spawn"]["child_policy"] == "ephemeral"
    assert cfg["spawn"]["child_runtime"]["memory_mb"] == 1024


def test_sandbox_rejects_unknown_policy():
    with pytest.raises(ValueError, match="sandbox\\(policy=\\.\\.\\.\\) must be one of"):
        sandbox(policy="invalid-policy")


def test_sandbox_rejects_spawn_limit_exceeded():
    with pytest.raises(ValueError, match="max_spawn_depth"):
        sandbox(
            policy="dedicated",
            allow_spawn=True,
            max_spawn_depth=99,
        )


def test_sandbox_rejects_non_list_spawn_to():
    with pytest.raises(ValueError, match="expects a list\\[str\\]"):
        sandbox(
            policy="dedicated",
            allow_spawn=True,
            spawn_to="researcher",  # type: ignore[arg-type]
        )


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


def test_runtime_includes_env_and_secret_refs():
    profile = runtime(
        env={"APP_MODE": "production", "MAX_RETRIES": 3},
        secrets=[
            Secret.from_name("provider-shared", required_keys=["OPENAI_API_KEY"]),
            Secret.from_dict("provider-local", {"OPENAI_API_KEY": "sk-local"}),
            "provider-shared",
        ],
    )
    assert profile["env"] == {"APP_MODE": "production", "MAX_RETRIES": "3"}
    assert profile["secret_refs"] == [
        {"name": "provider-shared", "required_keys": ["OPENAI_API_KEY"]},
        {"name": "provider-local"},
    ]
    assert "__secret_definitions" in profile
    assert len(profile["__secret_definitions"]) == 2


def test_from_env_prefers_api_key_over_legacy_access_token(monkeypatch, tmp_path):
    monkeypatch.setenv("ARA_API_BASE_URL", "https://api.ara.so")
    monkeypatch.setenv("ARA_API_KEY", "ara_api_key_primary_0123456789abcdef")
    monkeypatch.setenv("ARA_ACCESS_TOKEN", "legacy-token")

    client = core.AraClient.from_env(manifest=_manifest_with_runtime(runtime_profile={}), cwd=str(tmp_path))
    assert client.http.api_key == "ara_api_key_primary_0123456789abcdef"


def test_from_env_accepts_legacy_access_token_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("ARA_API_BASE_URL", "https://api.ara.so")
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.setenv("ARA_ACCESS_TOKEN", "legacy-token")

    client = core.AraClient.from_env(manifest=_manifest_with_runtime(runtime_profile={}), cwd=str(tmp_path))
    assert client.http.api_key == "legacy-token"


def test_runtime_duplicate_secret_name_keeps_first_definition():
    profile = runtime(
        secrets=[
            Secret.from_name("provider-shared", required_keys=["OPENAI_API_KEY"]),
            Secret.from_dict("provider-shared", {"OPENAI_API_KEY": "sk-overwrite-attempt"}),
        ],
    )
    assert profile["secret_refs"] == [
        {"name": "provider-shared", "required_keys": ["OPENAI_API_KEY"]},
    ]
    definitions = profile["__secret_definitions"]
    assert len(definitions) == 1
    assert definitions[0].values is None


def test_secret_rejects_reserved_keys():
    with pytest.raises(ValueError):
        runtime(env={"SESSION_ID": "abc"})
    with pytest.raises(ValueError):
        Secret.from_dict("provider-local", {"ARA_INTERNAL_TOKEN": "abc"})


def test_secret_from_dotenv_and_local_environ(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env.secrets"
    dotenv.write_text("OPENAI_API_KEY=sk-123\nANTHROPIC_API_KEY=an-123\n", encoding="utf-8")
    auto_secret = Secret.from_dotenv(filename=str(dotenv))
    assert auto_secret.name.startswith("sdk-dotenv-")
    assert auto_secret.values == {"OPENAI_API_KEY": "sk-123", "ANTHROPIC_API_KEY": "an-123"}
    dotenv_rotated = tmp_path / ".env.secrets.rotated"
    dotenv_rotated.write_text("OPENAI_API_KEY=sk-456\nANTHROPIC_API_KEY=an-789\n", encoding="utf-8")
    assert Secret.from_dotenv(filename=str(dotenv_rotated)).name == auto_secret.name

    secret = Secret.from_dotenv("provider-local", filename=str(dotenv))
    assert secret.name == "provider-local"
    assert secret.values == {"OPENAI_API_KEY": "sk-123", "ANTHROPIC_API_KEY": "an-123"}

    dict_secret = Secret.from_dict({"FOO": "bar"})
    assert dict_secret.name.startswith("sdk-dict-")
    assert Secret.from_dict({"FOO": "bar"}).name == dict_secret.name
    assert Secret.from_dict({"FOO": "baz"}).name == dict_secret.name
    with pytest.raises(ValueError, match="conflicts with name= keyword"):
        Secret.from_dict("provider-local", {"FOO": "bar"}, name="provider-other")

    monkeypatch.setenv("CAL_API_KEY", "cal-123")
    env_secret = Secret.from_local_environ("calendar", env_keys=["CAL_API_KEY"])
    assert env_secret.values == {"CAL_API_KEY": "cal-123"}


def test_secret_name_requires_two_or_more_characters():
    with pytest.raises(ValueError, match="Secret name must match"):
        Secret.from_name("a")

    secret = Secret.from_name("ab")
    assert secret.name == "ab"


class _FakeHttp:
    def __init__(self):
        self.calls: list[str] = []
        self.created_payload: dict | None = None
        self.secret_rows: list[dict[str, object]] = []

    def list_apps(self) -> dict:
        self.calls.append("list_apps")
        return {"apps": []}

    def create_app(self, body: dict) -> dict:
        self.calls.append("create_app")
        self.created_payload = body
        return {"app": {"id": "app_test_1"}}

    def update_app(self, app_id: str, body: dict) -> dict:
        self.calls.append("update_app")
        return {"app": {"id": app_id, **body}}

    def upsert_secret(self, app_id: str, *, name: str, values: dict[str, str]) -> dict:
        _ = app_id
        self.calls.append(f"upsert_secret:{name}")
        found = False
        for row in self.secret_rows:
            if str(row.get("name") or "") == name:
                row["key_names"] = sorted(values.keys())
                found = True
                break
        if not found:
            self.secret_rows.append({"name": name, "key_names": sorted(values.keys())})
        return {"secret": {"name": name, "key_names": sorted(values.keys())}}

    def list_secrets(self, app_id: str) -> dict:
        _ = app_id
        self.calls.append("list_secrets")
        return {"secrets": [dict(row) for row in self.secret_rows]}

    def delete_secret(self, app_id: str, name: str) -> None:
        _ = app_id
        self.calls.append(f"delete_secret:{name}")
        self.secret_rows = [row for row in self.secret_rows if str(row.get("name") or "") != name]

    def create_key(self, app_id: str, *, name: str, requests_per_minute: int) -> dict:
        _ = (app_id, name, requests_per_minute)
        self.calls.append("create_key")
        return {"key": "ak_app_test"}

    def list_x_keys(self, app_id: str) -> dict:
        _ = app_id
        self.calls.append("list_x_keys")
        return {"keys": []}

    def create_x_key(self, app_id: str, *, name: str, requests_per_minute: int) -> dict:
        _ = (app_id, name, requests_per_minute)
        self.calls.append("create_x_key")
        return {"id": "apk_x_test_1", "key": "aik_app_test", "key_prefix": "aik_app_test"}

    def revoke_x_key(self, app_id: str, key_id: str) -> None:
        _ = (app_id, key_id)
        self.calls.append("revoke_x_key")

    def run_app(
        self,
        app_id: str,
        *,
        runtime_key: str | None = None,
        app_header_key: str | None = None,
        agent_id: str | None,
        input_payload: dict,
        warmup: bool = False,
    ) -> dict:
        _ = (app_id, runtime_key, app_header_key, agent_id, input_payload, warmup)
        self.calls.append("run_app")
        return {"ok": True}

    def submit_async_run(
        self,
        app_id: str,
        *,
        runtime_key: str | None = None,
        app_header_key: str | None = None,
        agent_id: str | None,
        input_payload: dict,
        warmup: bool = False,
        run_id: str | None = None,
        idempotency_key: str | None = None,
        response_mode: str = "poll",
        callback: dict | None = None,
    ) -> dict:
        _ = (
            app_id,
            runtime_key,
            app_header_key,
            agent_id,
            input_payload,
            warmup,
            run_id,
            idempotency_key,
            response_mode,
            callback,
        )
        self.calls.append("submit_async_run")
        return {"ok": True, "run": {"run_id": run_id or "run_test_1", "status": "running"}}

    def get_async_run_status(
        self,
        app_id: str,
        run_id: str,
        *,
        runtime_key: str | None = None,
        app_header_key: str | None = None,
    ) -> dict:
        _ = (app_id, run_id, runtime_key, app_header_key)
        self.calls.append("get_async_run_status")
        return {"ok": True, "run": {"run_id": run_id, "status": "completed"}}

    def stream_logs(
        self,
        app_id: str,
        *,
        runtime_key: str | None = None,
        app_header_key: str | None = None,
    ):
        _ = (app_id, runtime_key, app_header_key)
        self.calls.append("stream_logs")
        yield {
            "timestamp": "2026-04-10T00:00:00Z",
            "level": "info",
            "run_id": "run_test_1",
            "event_type": "run.started",
            "message": "Run started",
        }


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


def test_deploy_syncs_local_secrets_before_warmup(tmp_path):
    runtime_profile = runtime(
        env={"APP_MODE": "dev"},
        secrets=[
            Secret.from_dict("provider-local", {"OPENAI_API_KEY": "sk-local"}),
            Secret.from_name("provider-shared", required_keys=["OPENAI_API_KEY"]),
        ],
    )
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )
    fake_http = _FakeHttp()
    client.http = fake_http

    out = client.deploy(warm=True, warm_agent_id="booking-coordinator")

    assert fake_http.created_payload is not None
    assert "__secret_definitions" not in fake_http.created_payload["runtime_profile"]
    assert fake_http.created_payload["runtime_profile"]["secret_refs"] == [
        {"name": "provider-local"},
        {"name": "provider-shared", "required_keys": ["OPENAI_API_KEY"]},
    ]
    assert fake_http.calls.index("upsert_secret:provider-local") < fake_http.calls.index("run_app")
    assert out["secrets"] == {
        "synced": ["provider-local"],
        "referenced_only": ["provider-shared"],
    }
    assert (tmp_path / ".runtime-key.local").exists()


def test_deploy_surfaces_backend_secrets_route_compat_error(tmp_path):
    runtime_profile = runtime(
        secrets=[Secret.from_dict("provider-local", {"OPENAI_API_KEY": "sk-local"})],
    )
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _CompatHttp(_FakeHttp):
        def upsert_secret(self, app_id: str, *, name: str, values: dict[str, str]) -> dict:
            _ = (app_id, name, values)
            raise RuntimeError(
                "POST /apps/app_test_1/secrets failed (404). "
                "Response body hidden by default; set ARA_SDK_DEBUG_HTTP_ERRORS=true to include it."
            )

    client.http = _CompatHttp()
    with pytest.raises(RuntimeError, match="does not support App SDK secret routes"):
        client.deploy()


def test_deploy_ignores_delete_404_for_concurrent_secret_reconciliation(tmp_path):
    local_secret = Secret.from_dict({"OPENAI_API_KEY": "sk-local"})
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile=runtime(secrets=[local_secret])),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _CompatDeleteHttp(_FakeHttp):
        def list_apps(self) -> dict:
            self.calls.append("list_apps")
            return {"apps": [{"id": "app_existing_1", "slug": "test-app", "role": "owner"}]}

        def delete_secret(self, app_id: str, name: str) -> None:
            _ = (app_id, name)
            raise RuntimeError(
                "DELETE /apps/app_existing_1/secrets/stale-secret failed (404). "
                "Response body hidden by default; set ARA_SDK_DEBUG_HTTP_ERRORS=true to include it."
            )

    fake_http = _CompatDeleteHttp()
    fake_http.secret_rows = [
        {"name": local_secret.name, "key_names": ["OPENAI_API_KEY"]},
        {"name": "stale-secret", "key_names": ["OLD_KEY"]},
    ]
    client.http = fake_http

    out = client.deploy()
    assert out["app_id"] == "app_existing_1"
    assert "delete_secret:stale-secret" not in fake_http.calls


def test_deploy_defaults_to_update_when_app_exists(tmp_path):
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _ExistingHttp(_FakeHttp):
        def list_apps(self) -> dict:
            self.calls.append("list_apps")
            return {"apps": [{"id": "app_existing_1", "slug": "test-app", "role": "owner"}]}

    fake_http = _ExistingHttp()
    client.http = fake_http

    out = client.deploy()

    assert out["app_id"] == "app_existing_1"
    assert "create_app" not in fake_http.calls
    assert "update_app" in fake_http.calls


def test_deploy_reconciles_app_secrets_to_runtime_refs(tmp_path):
    local_secret = Secret.from_dict({"OPENAI_API_KEY": "sk-local"})
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile=runtime(secrets=[local_secret])),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _ExistingHttp(_FakeHttp):
        def list_apps(self) -> dict:
            self.calls.append("list_apps")
            return {"apps": [{"id": "app_existing_1", "slug": "test-app", "role": "owner"}]}

    fake_http = _ExistingHttp()
    fake_http.secret_rows = [
        {"name": "stale-secret", "key_names": ["OLD_KEY"]},
        {"name": local_secret.name, "key_names": ["OPENAI_API_KEY"]},
    ]
    client.http = fake_http

    _ = client.deploy()

    assert "list_secrets" in fake_http.calls
    assert "delete_secret:stale-secret" in fake_http.calls
    assert f"delete_secret:{local_secret.name}" not in fake_http.calls


def test_deploy_without_runtime_secrets_does_not_reconcile_app_secrets(tmp_path):
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _ExistingHttp(_FakeHttp):
        def list_apps(self) -> dict:
            self.calls.append("list_apps")
            return {"apps": [{"id": "app_existing_1", "slug": "test-app", "role": "owner"}]}

    fake_http = _ExistingHttp()
    fake_http.secret_rows = [{"name": "stale-secret", "key_names": ["OLD_KEY"]}]
    client.http = fake_http

    _ = client.deploy()

    assert "list_secrets" not in fake_http.calls
    assert all(not call.startswith("delete_secret:") for call in fake_http.calls)


def test_setup_auth_creates_and_persists_app_header_key(tmp_path):
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _ExistingHttp(_FakeHttp):
        def list_apps(self) -> dict:
            self.calls.append("list_apps")
            return {"apps": [{"id": "app_existing_1", "slug": "test-app", "role": "owner"}]}

    fake_http = _ExistingHttp()
    client.http = fake_http

    out = client.setup_auth()

    assert out["app_id"] == "app_existing_1"
    assert out["app_header_key_present"] is True
    assert out["app_header_key_created"] is True
    assert (tmp_path / ".app-header-key.local").exists()
    assert "create_x_key" in fake_http.calls


def test_run_async_and_status_support_header_key(tmp_path):
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _ExistingHttp(_FakeHttp):
        def list_apps(self) -> dict:
            self.calls.append("list_apps")
            return {"apps": [{"id": "app_existing_1", "slug": "test-app", "role": "owner"}]}

    fake_http = _ExistingHttp()
    client.http = fake_http

    submit = client.run_async(
        agent_id="booking-coordinator",
        input_payload={"message": "hello"},
        app_header_key="aik_app_inline_key",
        run_id="run_inline_1",
        idempotency_key="run-inline-1",
    )
    status = client.run_status(run_id="run_inline_1", app_header_key="aik_app_inline_key")

    assert submit["ok"] is True
    assert status["ok"] is True
    assert "submit_async_run" in fake_http.calls
    assert "get_async_run_status" in fake_http.calls


def test_logs_accept_explicit_runtime_key(tmp_path):
    client = core.AraClient(
        manifest=_manifest_with_runtime(runtime_profile={}),
        api_base_url="https://api.ara.so",
        api_key="token",
        cwd=tmp_path,
    )

    class _ExistingHttp(_FakeHttp):
        def __init__(self):
            super().__init__()
            self.stream_call: dict | None = None

        def list_apps(self) -> dict:
            self.calls.append("list_apps")
            return {"apps": [{"id": "app_existing_1", "slug": "test-app", "role": "owner"}]}

        def stream_logs(
            self,
            app_id: str,
            *,
            runtime_key: str | None = None,
            app_header_key: str | None = None,
        ):
            self.stream_call = {
                "app_id": app_id,
                "runtime_key": runtime_key,
                "app_header_key": app_header_key,
            }
            yield from super().stream_logs(
                app_id,
                runtime_key=runtime_key,
                app_header_key=app_header_key,
            )

    fake_http = _ExistingHttp()
    client.http = fake_http

    rows = list(client.logs(runtime_key="ak_app_inline"))
    assert rows
    assert fake_http.stream_call is not None
    assert fake_http.stream_call["app_id"] == "app_existing_1"
    assert fake_http.stream_call["runtime_key"] == "ak_app_inline"
    assert fake_http.stream_call["app_header_key"] == ""


def test_cli_up_alias_dispatches_to_deploy(monkeypatch, capsys):
    class _StubClient:
        def __init__(self):
            self.kwargs: dict | None = None

        def deploy(self, **kwargs):
            self.kwargs = kwargs
            return {
                "app_id": "app_test_1",
                "slug": "test-app",
                "runtime_key_written": True,
                "runtime_key_path": "/tmp/.runtime-key.local",
                "warmup": {"runtime_key": "ak-secret"},
                "secrets": {
                    "synced": ["provider-local"],
                    "referenced_only": ["provider-shared"],
                    "values": {"OPENAI_API_KEY": "sk-local"},
                },
            }

    stub = _StubClient()

    monkeypatch.setattr(
        core.AraClient,
        "from_env",
        classmethod(lambda cls, *, manifest, cwd=None: stub),
    )

    core.run_cli(
        _manifest_with_runtime(runtime_profile={}),
        argv=["up", "--warm", "true"],
    )

    assert stub.kwargs is not None
    assert stub.kwargs["warm"] is True
    assert stub.kwargs["on_existing"] == "update"
    cli_out = capsys.readouterr().out
    assert '"ok": true' in cli_out.lower()
    assert '"slug": "test-app"' in cli_out.lower()
    assert '"runtime_key_written": true' in cli_out.lower()
    assert "OPENAI_API_KEY" not in cli_out
    assert "sk-local" not in cli_out


def test_cli_setup_auth_dispatches_to_client(monkeypatch, capsys):
    class _StubClient:
        def setup_auth(self, **kwargs):
            assert kwargs["x_key_name"] == "demo-x"
            assert kwargs["x_key_rpm"] == 55
            assert kwargs["ensure_runtime_key"] is True
            return {"ok": True, "app_id": "app_test_1", "app_header_key_present": True}

    stub = _StubClient()
    monkeypatch.setattr(
        core.AraClient,
        "from_env",
        classmethod(lambda cls, *, manifest, cwd=None: stub),
    )
    core.run_cli(
        _manifest_with_runtime(runtime_profile={}),
        argv=["setup-auth", "--x-key-name", "demo-x", "--x-key-rpm", "55", "--ensure-runtime-key", "true"],
    )
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()
    assert '"app_id": "app_test_1"' in out


def test_cli_local_does_not_require_api_key(monkeypatch, capsys):
    app = App(name="Local CLI App", project_name="local-cli-app")

    @app.local_entrypoint()
    def local(input_payload: dict[str, str]):
        return {"echo": str(input_payload.get("text") or "")}

    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)

    def _from_env_should_not_run(cls, *, manifest, cwd=None):  # noqa: ARG001
        raise AssertionError("AraClient.from_env should not run for local command")

    monkeypatch.setattr(
        core.AraClient,
        "from_env",
        classmethod(_from_env_should_not_run),
    )

    core.run_cli(app, argv=["local", "--input", "text=hello-local"])
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()
    assert '"echo": "hello-local"' in out


def test_cli_local_loads_dotenv_without_api_key(monkeypatch, tmp_path, capsys):
    app = App(name="Local CLI Env App", project_name="local-cli-env-app")

    @app.local_entrypoint()
    def local(_input_payload: dict[str, str]):
        return {"local_env": str(os.getenv("LOCAL_ONLY_VALUE") or "")}

    (tmp_path / ".env").write_text("LOCAL_ONLY_VALUE=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCAL_ONLY_VALUE", raising=False)
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)

    def _from_env_should_not_run(cls, *, manifest, cwd=None):  # noqa: ARG001
        raise AssertionError("AraClient.from_env should not run for local command")

    monkeypatch.setattr(
        core.AraClient,
        "from_env",
        classmethod(_from_env_should_not_run),
    )

    core.run_cli(app, argv=["local"])
    out = capsys.readouterr().out
    assert '"ok": true' in out.lower()
    assert '"local_env": "from-dotenv"' in out


def test_cli_logs_streams_runtime_lines(monkeypatch, capsys):
    class _StubClient:
        def logs(self):
            yield {
                "timestamp": "2026-04-10T01:02:03Z",
                "level": "info",
                "run_id": "run_abc123",
                "event_type": "run.started",
                "message": "Run started",
            }
            yield {
                "timestamp": "2026-04-10T01:02:04Z",
                "level": "error",
                "run_id": "run_abc123",
                "event_type": "run.failed",
                "message": "Tool failed",
            }

    stub = _StubClient()
    monkeypatch.setattr(
        core.AraClient,
        "from_env",
        classmethod(lambda cls, *, manifest, cwd=None: stub),
    )

    core.run_cli(
        _manifest_with_runtime(runtime_profile={}),
        argv=["logs"],
    )
    out = capsys.readouterr().out
    assert "run=run_abc123 event=run.started" in out
    assert "ERROR run=run_abc123 event=run.failed Tool failed" in out


def test_adapter_helpers_shapes():
    artifact = core.git_artifact("https://github.com/example/repo", ref="main", subdir="worker")
    assert artifact == {
        "type": "git",
        "repo_url": "https://github.com/example/repo",
        "ref": "main",
        "subdir": "worker",
    }

    adapter = core.command_adapter(
        "python3 worker.py",
        framework="custom",
        artifact=artifact,
        env={"FOO": "bar"},
    )
    assert adapter["type"] == "command"
    assert adapter["entrypoint"] == "python3 worker.py"
    assert adapter["artifact"]["type"] == "git"
    assert adapter["env"]["FOO"] == "bar"

    assert core.langgraph_adapter()["framework"] == "langgraph"
    assert core.langchain_adapter()["framework"] == "langchain"
    assert core.agno_adapter()["framework"] == "agno"


def test_event_envelope_generates_run_and_idempotency():
    out = core.event_envelope("channel.web.inbound", message="hello")
    event = out["event"]
    assert event["type"] == "channel.web.inbound"
    assert event["message"] == "hello"
    assert event["metadata"]["run_id"]
    assert event["metadata"]["idempotency_key"].startswith("channel-web-inbound-")

