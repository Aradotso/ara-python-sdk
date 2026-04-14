from __future__ import annotations

import json

from ara_sdk import core


def test_extract_connect_token_from_uri():
    token = core._extract_connect_token("ara://connect?token=abc123")
    assert token == "abc123"


def test_run_connect_cli_exchanges_token_and_outputs_commands(monkeypatch, tmp_path, capsys):
    private_key = tmp_path / "id_ed25519"
    public_key = tmp_path / "id_ed25519.pub"
    private_key.write_text("PRIVATE", encoding="utf-8")
    public_key.write_text("ssh-ed25519 AAAATESTKEY local", encoding="utf-8")
    proxy_token_file = tmp_path / "token dir" / "proxy token"
    captured: dict[str, str] = {}

    monkeypatch.setattr(core, "_resolve_api_base_url", lambda default=core.DEFAULT_API_BASE_URL: "https://api.ara.so")
    monkeypatch.setattr(core, "_resolve_control_plane_bearer", lambda: "ara_api_key_test")
    monkeypatch.setattr(core, "_ensure_local_ssh_keypair", lambda: (private_key, public_key))

    def _fake_upsert(alias: str, block: str):
        captured["alias"] = alias
        captured["block"] = block
        return tmp_path / "ssh_config"

    monkeypatch.setattr(core, "_upsert_ssh_config", _fake_upsert)
    monkeypatch.setattr(core, "_write_proxy_token_file", lambda token: proxy_token_file)

    def _fake_exchange(self, *, token: str, public_key: str, key_comment: str):
        _ = self
        assert token == "abc123"
        assert public_key.startswith("ssh-ed25519 ")
        assert key_comment
        return {
            "host_alias": "ara-personal",
            "proxy_token": "proxy_test_1",
            "session_id": "sess-123",
            "commands": {
                "ssh": "ssh ara-personal",
                "vscode": "code --remote ssh-remote+ara-personal /root/.ara/workspace",
                "sshfs_mount": "mkdir -p ~/AraWorkspace && sshfs ara-personal:/root/.ara/workspace ~/AraWorkspace",
            },
            "ssh_config": "\n".join(
                [
                    "Host ara-personal",
                    "  User root",
                    "  ProxyCommand ara ssh-proxy --token proxy_test_1",
                ]
            ),
        }

    monkeypatch.setattr(core._Http, "connect_exchange", _fake_exchange)

    core.run_connect_cli(["ara://connect?token=abc123"])

    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["host_alias"] == "ara-personal"
    assert body["session_id"] == "sess-123"
    assert body["commands"]["ssh"] == "ssh ara-personal"
    assert str(body["proxy_token_file"]).endswith("proxy token")
    assert f'IdentityFile "{private_key}"' in captured["block"]
    assert "ProxyCommand ara ssh-proxy --token-file " in captured["block"]
    assert "--token-file '" in captured["block"]
    assert "proxy token'" in captured["block"]
