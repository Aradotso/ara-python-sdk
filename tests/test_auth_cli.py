from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from ara_sdk import core


def _future_iso(minutes: int = 15) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_auth_login_saves_supabase_jwt_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _fake_cli_auth_config(self):
        _ = self
        return {
            "ok": True,
            "supabase_url": "https://try.ara.so",
            "supabase_anon_key": "anon_test",
        }

    def _fake_supabase_token_request(**kwargs):
        assert kwargs["grant_type"] == "password"
        return {
            "access_token": "jwt_access_1",
            "refresh_token": "refresh_1",
            "expires_in": 3600,
            "user": {"id": "u_test", "email": "u@test.local"},
        }

    def _fake_whoami(self):
        _ = self
        return {"ok": True, "user": {"id": "u_test", "email": "u@test.local"}}

    monkeypatch.setattr(core._Http, "cli_auth_config", _fake_cli_auth_config)
    monkeypatch.setattr(core, "_supabase_token_request", _fake_supabase_token_request)
    monkeypatch.setattr(core._Http, "cli_whoami", _fake_whoami)

    core.run_auth_cli(
        [
            "login",
            "--api-base-url",
            "https://api.ara.so",
            "--email",
            "u@test.local",
            "--password",
            "pw",
        ]
    )

    creds_path = tmp_path / ".ara" / "credentials.json"
    payload = json.loads(creds_path.read_text(encoding="utf-8"))
    assert payload["auth_type"] == "supabase_jwt"
    assert payload["access_token"] == "jwt_access_1"
    assert payload["refresh_token"] == "refresh_1"
    assert payload["api_base_url"] == "https://api.ara.so"


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
