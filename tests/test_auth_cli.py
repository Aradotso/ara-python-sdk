from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from ara_sdk import core


def _future_iso(minutes: int = 15) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_auth_login_defaults_to_polling_pkce(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    status_responses = iter(
        [
            {
                "ok": True,
                "status": "approved",
                "auth_code": "oauth_code_default_poll",
                "state": "state_default_poll",
                "redirect_uri": "https://api.ara.so/auth/cli/device/callback?sid=sess_default",
            },
        ]
    )

    def _fake_cli_auth_config(self):
        _ = self
        return {
            "ok": True,
            "supabase_url": "https://try.ara.so",
            "supabase_anon_key": "anon_test",
            "api_base_url": "https://api.ara.so",
        }

    def _fake_device_start(self, **kwargs):
        _ = self
        assert kwargs["provider"] == "google"
        assert kwargs["code_challenge"]
        assert kwargs["timeout_seconds"] == 180
        return {
            "ok": True,
            "session_id": "sess_default",
            "poll_token": "poll_tok_default",
            "authorize_url": "https://try.ara.so/auth/v1/authorize?x=default",
            "interval_seconds": 1,
            "expires_at": _future_iso(5),
        }

    def _fake_device_status(self, **kwargs):
        _ = self
        assert kwargs["session_id"] == "sess_default"
        assert kwargs["poll_token"] == "poll_tok_default"
        return next(status_responses)

    def _fake_supabase_token_request(**kwargs):
        assert kwargs["grant_type"] == "pkce"
        assert kwargs["body"]["auth_code"] == "oauth_code_default_poll"
        assert kwargs["body"]["code_verifier"]
        assert kwargs["body"]["redirect_uri"] == "https://api.ara.so/auth/cli/device/callback?sid=sess_default"
        return {
            "access_token": "jwt_access_pkce",
            "refresh_token": "refresh_pkce",
            "expires_in": 3600,
            "user": {"id": "u_test", "email": "oauth@test.local"},
        }

    def _fake_whoami(self):
        _ = self
        return {"ok": True, "user": {"id": "u_test", "email": "oauth@test.local"}}

    monkeypatch.setattr(core._Http, "cli_auth_config", _fake_cli_auth_config)
    monkeypatch.setattr(core._Http, "cli_auth_device_start", _fake_device_start)
    monkeypatch.setattr(core._Http, "cli_auth_device_status", _fake_device_status)
    monkeypatch.setattr(core, "_supabase_token_request", _fake_supabase_token_request)
    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)

    core.run_auth_cli(
        [
            "login",
            "--no-browser",
        ]
    )

    creds_path = tmp_path / ".ara" / "credentials.json"
    payload = json.loads(creds_path.read_text(encoding="utf-8"))
    assert payload["auth_type"] == "supabase_jwt"
    assert payload["access_token"] == "jwt_access_pkce"
    assert payload["refresh_token"] == "refresh_pkce"
    assert payload["api_base_url"] == "https://api.ara.so"


def test_auth_login_poll_flow_uses_device_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    status_responses = iter(
        [
            {
                "ok": True,
                "status": "approved",
                "auth_code": "oauth_code_poll_1",
                "state": "state_poll_1",
                "redirect_uri": "https://api.ara.so/auth/cli/device/callback?sid=sess_1",
            },
        ]
    )

    def _fake_cli_auth_config(self):
        _ = self
        return {
            "ok": True,
            "supabase_url": "https://try.ara.so",
            "supabase_anon_key": "anon_test",
            "api_base_url": "https://api.ara.so",
        }

    def _fake_device_start(self, **kwargs):
        _ = self
        assert kwargs["provider"] == "google"
        assert kwargs["code_challenge"]
        return {
            "ok": True,
            "session_id": "sess_1",
            "poll_token": "poll_tok_1",
            "authorize_url": "https://try.ara.so/auth/v1/authorize?x=1",
            "interval_seconds": 1,
            "expires_at": _future_iso(5),
        }

    def _fake_device_status(self, **kwargs):
        _ = self
        assert kwargs["session_id"] == "sess_1"
        assert kwargs["poll_token"] == "poll_tok_1"
        return next(status_responses)

    def _fake_supabase_token_request(**kwargs):
        assert kwargs["grant_type"] == "pkce"
        assert kwargs["body"]["auth_code"] == "oauth_code_poll_1"
        return {
            "access_token": "jwt_access_poll",
            "refresh_token": "refresh_poll",
            "expires_in": 3600,
            "user": {"id": "u_test", "email": "oauth@test.local"},
        }

    def _fake_whoami(self):
        _ = self
        return {"ok": True, "user": {"id": "u_test", "email": "oauth@test.local"}}

    monkeypatch.setattr(core._Http, "cli_auth_config", _fake_cli_auth_config)
    monkeypatch.setattr(core._Http, "cli_auth_device_start", _fake_device_start)
    monkeypatch.setattr(core._Http, "cli_auth_device_status", _fake_device_status)
    monkeypatch.setattr(core, "_supabase_token_request", _fake_supabase_token_request)
    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)

    def _unexpected_localhost(**kwargs):
        raise AssertionError("localhost callback should not be used for --auth-flow poll")

    monkeypatch.setattr(core, "_collect_oauth_callback_via_localhost", _unexpected_localhost)
    core.run_auth_cli(["login", "--auth-flow", "poll", "--no-browser"])

    payload = json.loads((tmp_path / ".ara" / "credentials.json").read_text(encoding="utf-8"))
    assert payload["auth_type"] == "supabase_jwt"
    assert payload["access_token"] == "jwt_access_poll"
    assert payload["refresh_token"] == "refresh_poll"


@pytest.mark.parametrize("legacy_auth_flow", ["auto", "localhost"])
def test_auth_login_rejects_legacy_auth_flows(legacy_auth_flow):
    with pytest.raises(SystemExit):
        core.run_auth_cli(["login", "--auth-flow", legacy_auth_flow])


def test_auth_login_poll_flow_fails_fast_when_code_already_consumed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _fake_cli_auth_config(self):
        _ = self
        return {
            "ok": True,
            "supabase_url": "https://try.ara.so",
            "supabase_anon_key": "anon_test",
            "api_base_url": "https://api.ara.so",
        }

    def _fake_device_start(self, **kwargs):
        _ = (self, kwargs)
        return {
            "ok": True,
            "session_id": "sess_consumed",
            "poll_token": "poll_tok_consumed",
            "authorize_url": "https://try.ara.so/auth/v1/authorize?x=consumed",
            "interval_seconds": 1,
            "expires_at": _future_iso(5),
        }

    def _fake_device_status(self, **kwargs):
        _ = (self, kwargs)
        return {
            "ok": False,
            "status": "consumed",
            "error": "auth_code_already_consumed",
            "error_description": "This login code has already been used.",
        }

    monkeypatch.setattr(core._Http, "cli_auth_config", _fake_cli_auth_config)
    monkeypatch.setattr(core._Http, "cli_auth_device_start", _fake_device_start)
    monkeypatch.setattr(core._Http, "cli_auth_device_status", _fake_device_status)

    with pytest.raises(SystemExit, match=r"OAuth login code already used"):
        core.run_auth_cli(["login", "--auth-flow", "poll", "--no-browser"])


def test_auth_login_with_api_key_saves_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _fake_whoami(self):
        _ = self
        return {"ok": True, "user": {"id": "u_test", "email": "u@test.local"}}

    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)
    core.run_auth_cli(
        [
            "login",
            "--api-base-url",
            "https://api.ara.so",
            "--api-key",
            "ara_api_key_test_123",
        ]
    )

    payload = json.loads((tmp_path / ".ara" / "credentials.json").read_text(encoding="utf-8"))
    assert payload["auth_type"] == "cli_api_key"
    assert payload["api_key"] == "ara_api_key_test_123"
    assert payload["api_base_url"] == "https://api.ara.so"


def test_auth_login_with_api_key_warns_when_unverified(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _fake_whoami(self):
        _ = self
        raise RuntimeError("boom")

    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)
    core.run_auth_cli(
        [
            "login",
            "--api-base-url",
            "https://api.ara.so",
            "--api-key",
            "ara_api_key_test_123",
        ]
    )

    err = capsys.readouterr().err
    assert "could not verify API key against server" in err


def test_auth_login_rejects_removed_password_flags():
    with pytest.raises(SystemExit):
        core.run_auth_cli(["login", "--email", "u@test.local", "--password", "pw"])


def test_auth_login_rejects_unknown_provider(monkeypatch):
    monkeypatch.delenv("ARA_API_BASE_URL", raising=False)
    with pytest.raises(SystemExit, match=r"unsupported OAuth provider"):
        core.run_auth_cli(["login", "--provider", "evil-provider"])


def test_auth_login_reports_already_logged_in_without_reauth(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)
    creds_dir = tmp_path / ".ara"
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / "credentials.json").write_text(
        json.dumps(
            {
                "auth_type": "supabase_jwt",
                "api_base_url": "https://api.ara.so",
                "supabase_url": "https://try.ara.so",
                "supabase_anon_key": "anon_test",
                "access_token": "jwt_access_existing",
                "refresh_token": "refresh_existing",
                "expires_at": _future_iso(),
                "user": {"id": "u_existing", "email": "sven@ara.so"},
            }
        ),
        encoding="utf-8",
    )

    def _fake_whoami(self):
        _ = self
        return {"ok": True, "user": {"id": "u_existing", "email": "sven@ara.so"}}

    def _unexpected_device_start(self, **kwargs):
        _ = (self, kwargs)
        raise AssertionError("OAuth flow should not start when already authenticated")

    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)
    monkeypatch.setattr(core._Http, "cli_auth_device_start", _unexpected_device_start)

    core.run_auth_cli(["login", "--no-browser"])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "already_logged_in"
    assert out["auth_source"] == "supabase_jwt"
    assert out["user"]["email"] == "sven@ara.so"
    assert "ara auth logout" in out["message"]
    assert "--reauth" in out["message"]


def test_auth_login_reauth_forces_fresh_oauth_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)
    creds_dir = tmp_path / ".ara"
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / "credentials.json").write_text(
        json.dumps(
            {
                "auth_type": "supabase_jwt",
                "api_base_url": "https://api.ara.so",
                "supabase_url": "https://try.ara.so",
                "supabase_anon_key": "anon_test",
                "access_token": "jwt_access_old",
                "refresh_token": "refresh_old",
                "expires_at": _future_iso(),
                "user": {"id": "u_old", "email": "old@test.local"},
            }
        ),
        encoding="utf-8",
    )

    def _fake_cli_auth_config(self):
        _ = self
        return {
            "ok": True,
            "supabase_url": "https://try.ara.so",
            "supabase_anon_key": "anon_test",
            "api_base_url": "https://api.ara.so",
        }

    def _fake_device_start(self, **kwargs):
        _ = self
        assert kwargs["provider"] == "google"
        return {
            "ok": True,
            "session_id": "sess_reauth",
            "poll_token": "poll_reauth",
            "authorize_url": "https://try.ara.so/auth/v1/authorize?x=reauth",
            "interval_seconds": 1,
            "expires_at": _future_iso(5),
        }

    def _fake_device_status(self, **kwargs):
        _ = self
        assert kwargs["session_id"] == "sess_reauth"
        assert kwargs["poll_token"] == "poll_reauth"
        return {
            "ok": True,
            "status": "approved",
            "auth_code": "oauth_code_reauth",
            "state": "state_reauth",
            "redirect_uri": "https://api.ara.so/auth/cli/device/callback?sid=sess_reauth",
        }

    def _fake_supabase_token_request(**kwargs):
        assert kwargs["grant_type"] == "pkce"
        assert kwargs["body"]["auth_code"] == "oauth_code_reauth"
        return {
            "access_token": "jwt_access_new",
            "refresh_token": "refresh_new",
            "expires_in": 3600,
            "user": {"id": "u_new", "email": "new@test.local"},
        }

    def _fake_whoami(self):
        _ = self
        return {"ok": True, "user": {"id": "u_new", "email": "new@test.local"}}

    monkeypatch.setattr(core._Http, "cli_auth_config", _fake_cli_auth_config)
    monkeypatch.setattr(core._Http, "cli_auth_device_start", _fake_device_start)
    monkeypatch.setattr(core._Http, "cli_auth_device_status", _fake_device_status)
    monkeypatch.setattr(core, "_supabase_token_request", _fake_supabase_token_request)
    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)

    core.run_auth_cli(["login", "--reauth", "--no-browser"])
    payload = json.loads((creds_dir / "credentials.json").read_text(encoding="utf-8"))
    assert payload["access_token"] == "jwt_access_new"
    assert payload["refresh_token"] == "refresh_new"
    assert payload["user"]["email"] == "new@test.local"


def test_auth_login_bad_oauth_state_includes_stale_url_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _fake_cli_auth_config(self):
        _ = self
        return {
            "ok": True,
            "supabase_url": "https://try.ara.so",
            "supabase_anon_key": "anon_test",
            "api_base_url": "https://api.ara.so",
        }

    def _fake_device_start(self, **kwargs):
        _ = (self, kwargs)
        return {
            "ok": True,
            "session_id": "sess_bad_state",
            "poll_token": "poll_bad_state",
            "authorize_url": "https://try.ara.so/auth/v1/authorize?x=bad-state",
            "interval_seconds": 1,
            "expires_at": _future_iso(5),
        }

    def _fake_device_status(self, **kwargs):
        _ = (self, kwargs)
        return {
            "ok": False,
            "status": "error",
            "error": "invalid_request",
            "error_description": "OAuth state parameter is invalid (bad_oauth_state)",
        }

    monkeypatch.setattr(core._Http, "cli_auth_config", _fake_cli_auth_config)
    monkeypatch.setattr(core._Http, "cli_auth_device_start", _fake_device_start)
    monkeypatch.setattr(core._Http, "cli_auth_device_status", _fake_device_status)

    with pytest.raises(SystemExit) as exc:
        core.run_auth_cli(["login", "--auth-flow", "poll", "--no-browser"])
    message = str(exc.value)
    assert "bad_oauth_state" in message
    assert "stale or already consumed" in message
    # Regression guard: hint should be appended once even when wrapped by run_auth_cli.
    assert message.count("use `ara auth whoami` or `ara auth logout` first") == 1


def test_oauth_callback_port_env_requires_valid_range(monkeypatch):
    monkeypatch.setenv("ARA_CLI_OAUTH_PORT", "70000")
    with pytest.raises(RuntimeError, match=r"must be 1-65535"):
        core._collect_oauth_callback_via_localhost(
            supabase_url="https://try.ara.so",
            provider="google",
            code_challenge="challenge",
            expected_state="expected",
            timeout_seconds=30,
            open_browser=False,
        )


def test_resolve_control_plane_bearer_refreshes_expired_cli_jwt(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    creds_dir = tmp_path / ".ara"
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / "credentials.json").write_text(
        json.dumps(
            {
                "auth_type": "supabase_jwt",
                "api_base_url": "https://api.ara.so",
                "supabase_url": "https://try.ara.so",
                "supabase_anon_key": "anon_test",
                "access_token": "expired_token",
                "refresh_token": "refresh_1",
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)

    def _fake_supabase_token_request(**kwargs):
        assert kwargs["grant_type"] == "refresh_token"
        return {
            "access_token": "fresh_access_token",
            "refresh_token": "refresh_2",
            "expires_in": 3600,
            "user": {"id": "u_test", "email": "u@test.local"},
        }

    monkeypatch.setattr(core, "_supabase_token_request", _fake_supabase_token_request)
    token = core._resolve_control_plane_bearer()
    assert token == "fresh_access_token"
    payload = json.loads((creds_dir / "credentials.json").read_text(encoding="utf-8"))
    assert payload["access_token"] == "fresh_access_token"
    assert payload["refresh_token"] == "refresh_2"


def test_from_env_accepts_cli_jwt_credentials_for_app_client(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    creds_dir = tmp_path / ".ara"
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / "credentials.json").write_text(
        json.dumps(
            {
                "auth_type": "supabase_jwt",
                "api_base_url": "https://api.local.ara.so",
                "supabase_url": "https://try.ara.so",
                "supabase_anon_key": "anon_test",
                "access_token": "jwt_access_1",
                "refresh_token": "refresh_1",
                "expires_at": _future_iso(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("ARA_API_BASE_URL", raising=False)

    manifest = {
        "name": "Test App",
        "slug": "test-app",
        "description": "",
        "agent": {},
        "workflows": [],
        "interfaces": {},
        "runtime_profile": {},
    }
    app_client = core.AraClient.from_env(manifest=manifest, cwd=str(tmp_path))
    assert app_client.http.api_key == "jwt_access_1"
    assert app_client.http.base_url == "https://api.local.ara.so"


def test_auth_logout_removes_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    creds_dir = tmp_path / ".ara"
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / "credentials.json").write_text("{}", encoding="utf-8")

    core.run_auth_cli(["logout"])
    assert not (creds_dir / "credentials.json").exists()


def test_save_cli_credentials_writes_file_with_0600_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    core._save_cli_credentials({"auth_type": "supabase_jwt", "access_token": "jwt", "refresh_token": "r"})

    creds_dir = tmp_path / ".ara"
    creds_path = tmp_path / ".ara" / "credentials.json"
    dir_mode = stat.S_IMODE(creds_dir.stat().st_mode)
    mode = stat.S_IMODE(creds_path.stat().st_mode)
    assert dir_mode == 0o700
    assert mode == 0o600


def test_coerce_supabase_expiry_iso_raises_when_missing_expiry():
    with pytest.raises(RuntimeError, match="did not include a valid expires_at or expires_in"):
        core._coerce_supabase_expiry_iso({"access_token": "x"})


def test_auth_whoami_reports_cli_api_key_source(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)
    (tmp_path / ".ara").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ara" / "credentials.json").write_text(
        json.dumps(
            {
                "api_key": "ara_api_key_plain",
                "api_base_url": "https://api.ara.so",
            }
        ),
        encoding="utf-8",
    )

    def _fake_whoami(self):
        _ = self
        return {"ok": True, "user": {"id": "u_test", "email": "u@test.local"}}

    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)
    core.run_auth_cli(["whoami"])
    out = json.loads(capsys.readouterr().out)
    assert out["auth_source"] == "cli_api_key"


def test_current_authenticated_identity_handles_non_runtime_bearer_errors(monkeypatch):
    def _boom():
        raise ValueError("bad credentials payload")

    monkeypatch.setattr(core, "_resolve_control_plane_bearer", _boom)
    assert core._current_authenticated_identity("https://api.ara.so") is None


def test_current_authenticated_identity_handles_non_runtime_whoami_errors(monkeypatch):
    monkeypatch.setattr(core, "_resolve_control_plane_bearer", lambda: "token")

    def _boom(self):
        _ = self
        raise json.JSONDecodeError("malformed", "bad", 0)

    monkeypatch.setattr(core._Http, "cli_whoami", _boom)
    assert core._current_authenticated_identity("https://api.ara.so") is None


def test_resolve_auth_source_label_returns_unknown_for_malformed_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARA_API_KEY", raising=False)
    monkeypatch.delenv("ARA_ACCESS_TOKEN", raising=False)
    (tmp_path / ".ara").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ara" / "credentials.json").write_text("{}", encoding="utf-8")

    assert core._resolve_auth_source_label() == "unknown"
