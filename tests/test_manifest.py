import io
import urllib.error

import pytest

from ara_sdk import App, cron, runtime, sandbox
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

    http = core._Http(base_url="https://api.ara.so", access_token="test-token")
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

    http = core._Http(base_url="https://api.ara.so", access_token="test-token")
    with pytest.raises(RuntimeError) as exc:
        http.list_apps()

    message = str(exc.value)
    assert "GET /apps failed (504):" in message
    assert details in message

