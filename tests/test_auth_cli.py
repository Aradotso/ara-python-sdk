from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from ara_sdk import core


def _future_iso(minutes: int = 15) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_auth_login_defaults_to_oauth_pkce(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _fake_cli_auth_config(self):
        _ = self
        return {
            "ok": True,
            "supabase_url": "https://try.ara.so",
            "supabase_anon_key": "anon_test",
            "api_base_url": "https://api.ara.so",
        }

    def _fake_callback(**kwargs):
        assert kwargs["provider"] == "google"
        assert kwargs["open_browser"] is False
        assert kwargs["supabase_url"] == "https://try.ara.so"
        assert kwargs["expected_state"]
        return {
            "code": "oauth_code_1",
            "state": kwargs["expected_state"],
            "redirect_uri": "http://127.0.0.1:53682/auth/callback",
        }

    def _fake_supabase_token_request(**kwargs):
        assert kwargs["grant_type"] == "pkce"
        assert kwargs["body"]["auth_code"] == "oauth_code_1"
        assert kwargs["body"]["code_verifier"]
        assert kwargs["body"]["redirect_uri"] == "http://127.0.0.1:53682/auth/callback"
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
    monkeypatch.setattr(core, "_collect_oauth_callback_via_localhost", _fake_callback)
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


def test_from_env_accepts_cli_jwt_credentials(monkeypatch, tmp_path):
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
    runtime_client = core.AraRuntimeClient.from_env(cwd=str(tmp_path))

    assert app_client.http.api_key == "jwt_access_1"
    assert app_client.http.base_url == "https://api.local.ara.so"
    assert runtime_client.http.api_key == "jwt_access_1"
    assert runtime_client.http.base_url == "https://api.local.ara.so"


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
