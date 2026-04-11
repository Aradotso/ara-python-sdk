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

