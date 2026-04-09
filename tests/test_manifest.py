import io
import urllib.error

import pytest

from ara_sdk import App, Secret, cron, runtime, sandbox
from ara_sdk import core


def test_app_manifest_project_name_slug_priority():
    app = App(name="Investor Booker", project_name="Team Internal App")
    assert app.slug == "team-internal-app"


def test_subagent_registers_profile_and_workflow():
    app = App(name="Test App")

    @app.subagent(
        id="booking-coordinator",
        workflow_id="booking-coordinator",
        instructions="Coordinate booking tasks.",
        schedule=cron("0 10 * * 1-5"),
        runtime=runtime(memory_mb=1024),
        sandbox=sandbox(max_concurrency=3),
    )
    def booking():
        """Coordinate booking workflows."""

    manifest = app.manifest
    profiles = manifest["agent"]["profiles"]
    workflows = manifest["workflows"]
    subagents = manifest["agent"]["subagents"]

    assert profiles[0]["id"] == "booking-coordinator"
    assert workflows[0]["id"] == "booking-coordinator"
    assert workflows[0]["trigger"]["type"] == "cron"
    assert subagents[0]["sandbox"]["max_concurrency"] == 3


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
    secret = Secret.from_dotenv("provider-local", filename=str(dotenv))
    assert secret.name == "provider-local"
    assert secret.values == {"OPENAI_API_KEY": "sk-123", "ANTHROPIC_API_KEY": "an-123"}

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
        return {"secret": {"name": name, "key_names": sorted(values.keys())}}

    def create_key(self, app_id: str, *, name: str, requests_per_minute: int) -> dict:
        _ = (app_id, name, requests_per_minute)
        self.calls.append("create_key")
        return {"key": "ak_app_test"}

    def run_app(
        self,
        app_id: str,
        *,
        runtime_key: str,
        workflow_id: str | None,
        input_payload: dict,
        warmup: bool = False,
    ) -> dict:
        _ = (app_id, runtime_key, workflow_id, input_payload, warmup)
        self.calls.append("run_app")
        return {"ok": True}


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

    out = client.deploy(warm=True, warm_workflow_id="warmup-flow")

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

