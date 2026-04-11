from __future__ import annotations

import pytest

from ara_sdk import __main__ as sdk_main


def test_standalone_cli_dispatches_command_to_run_cli(tmp_path, monkeypatch):
    script = tmp_path / "app.py"
    script.write_text(
        "\n".join(
            [
                "from ara_sdk import App",
                "app = App('Standalone CLI Probe', project_name='standalone-cli-probe')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _run_cli(app, argv=None, *, default_command="deploy"):
        captured["app_name"] = getattr(app, "name", "")
        captured["argv"] = list(argv or [])
        captured["default_command"] = default_command

    monkeypatch.setattr(sdk_main, "run_cli", _run_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "deploy", str(script), "--warm", "false"],
    )

    sdk_main.main()

    assert captured["app_name"] == "Standalone CLI Probe"
    assert captured["argv"] == ["deploy", "--warm", "false"]
    assert captured["default_command"] == "deploy"


def test_standalone_cli_usage_mentions_invoked_binary(monkeypatch):
    monkeypatch.setattr(sdk_main.sys, "argv", ["ara"])
    with pytest.raises(SystemExit, match=r"Usage: ara <command> <app_script.py> \[args...\]"):
        sdk_main.main()


def test_standalone_cli_help_lists_top_level_commands(monkeypatch, capsys):
    monkeypatch.setattr(sdk_main.sys, "argv", ["ara", "--help"])

    sdk_main.main()

    out = capsys.readouterr().out
    assert "App commands (require <app_script.py>):" in out
    assert "auth      login/whoami/logout for CLI auth" in out
    assert "runtime   runtime capabilities, tools, skills, and control APIs" in out


def test_runtime_cli_dispatches_without_app_script(monkeypatch):
    captured: dict[str, object] = {}

    def _run_runtime_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_runtime_cli", _run_runtime_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "runtime", "capabilities", "--session", "sess-123"],
    )

    sdk_main.main()

    assert captured["argv"] == ["capabilities", "--session", "sess-123"]


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

