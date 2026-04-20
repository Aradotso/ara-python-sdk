from __future__ import annotations

import pytest

from ara_sdk import __main__ as sdk_main


def test_standalone_cli_discovers_minimal_automation_script(tmp_path, monkeypatch):
    script = tmp_path / "automation.py"
    script.write_text(
        "\n".join(
            [
                "import ara_sdk as ara",
                "",
                "@ara.tool",
                "def send_email(to: str, subject: str, body: str) -> dict:",
                "    return {'ok': True}",
                "",
                "ara.Automation(",
                "    'weekday-priority-agent',",
                "    tools=[send_email],",
                "    system_instructions='Send weekday digest.',",
                ")",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _run_app_cli(app, argv=None, *, default_command="deploy"):
        captured["app_name"] = getattr(app, "name", "")
        captured["argv"] = list(argv or [])
        captured["default_command"] = default_command

    monkeypatch.setattr(sdk_main, "_run_app_cli", _run_app_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "deploy", str(script)],
    )

    sdk_main.main()

    assert captured["app_name"] == "weekday-priority-agent"
    assert captured["argv"] == ["deploy"]
    assert captured["default_command"] == "deploy"


def test_standalone_cli_usage_mentions_automation_script(monkeypatch):
    monkeypatch.setattr(sdk_main.sys, "argv", ["ara"])
    with pytest.raises(SystemExit, match=r"Usage: ara <command> <automation_script.py> \[args...\]"):
        sdk_main.main()


def test_standalone_cli_help_lists_minimal_command_surface(monkeypatch, capsys):
    monkeypatch.setattr(sdk_main.sys, "argv", ["ara", "--help"])

    sdk_main.main()

    out = capsys.readouterr().out
    assert "Automation commands (require <automation_script.py>):" in out
    assert "deploy, up, run, logs" in out
    assert "--message" not in out
    assert "auth      login/whoami/logout/rotate for CLI auth" in out
    assert "runtime   runtime capabilities + tools/files/exec operations" in out
    assert "--update  self-update ara CLI to latest ara-sdk" in out


def test_auth_group_prints_group_help_without_subcommand(monkeypatch):
    captured: dict[str, object] = {}

    def _run_auth_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_auth_cli", _run_auth_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "auth"],
    )

    sdk_main.main()

    assert captured["argv"] == ["--help"]


def test_runtime_group_prints_group_help_without_subcommand(monkeypatch):
    captured: dict[str, object] = {}

    def _run_runtime_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_runtime_cli", _run_runtime_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "runtime"],
    )

    sdk_main.main()

    assert captured["argv"] == ["--help"]


def test_runtime_group_forwards_runtime_arguments(monkeypatch):
    captured: dict[str, object] = {}

    def _run_runtime_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_runtime_cli", _run_runtime_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "runtime", "tools", "available", "--session", "sess_123"],
    )

    sdk_main.main()

    assert captured["argv"] == ["tools", "available", "--session", "sess_123"]


def test_update_flag_routes_to_update_cli(monkeypatch):
    captured: dict[str, object] = {}

    def _run_update_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_update_cli", _run_update_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "--update"],
    )

    sdk_main.main()

    assert captured["argv"] == []


def test_update_command_routes_to_update_cli_with_args(monkeypatch):
    captured: dict[str, object] = {}

    def _run_update_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_update_cli", _run_update_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "update", "--dry-run"],
    )

    sdk_main.main()

    assert captured["argv"] == ["--dry-run"]


def test_removed_global_groups_require_script_and_fail_usage(monkeypatch):
    for command in ("session", "automation", "connect", "ssh-proxy", "start", "status", "stop"):
        monkeypatch.setattr(sdk_main.sys, "argv", ["ara", command])
        with pytest.raises(SystemExit, match=r"Usage: ara <command> <automation_script.py> \[args...\]"):
            sdk_main.main()
