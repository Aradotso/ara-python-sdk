from __future__ import annotations

import pytest

from ara_sdk import __main__ as sdk_main


def test_standalone_cli_dispatches_command_to_internal_app_cli(tmp_path, monkeypatch):
    script = tmp_path / "app.py"
    script.write_text(
        "\n".join(
            [
                "from ara_sdk import App",
                "app = App('standalone-cli-probe')",
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
        ["ara", "deploy", str(script), "--warm", "false"],
    )

    sdk_main.main()

    assert captured["app_name"] == "standalone-cli-probe"
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
    assert "auth      login/whoami/logout/rotate for CLI auth" in out
    assert "runtime   runtime capabilities, tools, skills, and control APIs" in out
    assert "session   start/status/stop authenticated cloud sessions" in out


def test_standalone_cli_invalid_project_name_shows_dns_hint(tmp_path, monkeypatch):
    script = tmp_path / "bad_app.py"
    script.write_text(
        "\n".join(
            [
                "from ara_sdk import App",
                "app = App('Bad_Name')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "deploy", str(script)],
    )

    with pytest.raises(SystemExit, match=r"Invalid App project_name"):
        sdk_main.main()


def test_standalone_cli_reraises_non_project_name_value_errors(tmp_path, monkeypatch):
    script = tmp_path / "bad_app.py"
    script.write_text("app = object()\n", encoding="utf-8")

    def _raise_unrelated_value_error(_path):
        raise ValueError("bad secret config")

    monkeypatch.setattr(sdk_main, "_load_module", _raise_unrelated_value_error)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "deploy", str(script)],
    )

    with pytest.raises(ValueError, match=r"bad secret config"):
        sdk_main.main()


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


def test_session_group_dispatches_without_app_script(monkeypatch):
    captured: dict[str, object] = {}

    def _run_runtime_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_runtime_cli", _run_runtime_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "session", "status"],
    )

    sdk_main.main()

    assert captured["argv"] == ["session", "status"]


def test_session_alias_dispatches_to_runtime_session(monkeypatch):
    captured: dict[str, object] = {}

    def _run_runtime_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_runtime_cli", _run_runtime_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "start"],
    )

    sdk_main.main()

    assert captured["argv"] == ["session", "start"]


def test_session_alias_forwards_trailing_args(monkeypatch):
    captured: dict[str, object] = {}

    def _run_runtime_cli(argv=None):
        captured["argv"] = list(argv or [])

    monkeypatch.setattr(sdk_main, "run_runtime_cli", _run_runtime_cli)
    monkeypatch.setattr(
        sdk_main.sys,
        "argv",
        ["ara", "status", "--json"],
    )

    sdk_main.main()

    assert captured["argv"] == ["session", "status", "--json"]


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

