from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

from .core import App, _run_app_cli, run_auth_cli, run_runtime_cli


def _print_help(bin_name: str) -> None:
    print(
        "\n".join(
            [
                f"Usage: {bin_name} <command> <app_script.py> [args...]",
                "",
                "App commands (require <app_script.py>):",
                "  deploy, up, run, run-async, run-status, logs, events, setup, setup-auth, invite",
                "",
                "Global command groups (no app script required):",
                "  auth      login/whoami/logout/rotate for CLI auth",
                "  runtime   runtime capabilities, tools, skills, and control APIs",
                "",
                "Examples:",
                f"  {bin_name} deploy app.py",
                f"  {bin_name} run app.py --agent booking_coordinator --message \"hello\"",
                f"  {bin_name} auth login",
                f"  {bin_name} runtime capabilities --session sess-123",
                "",
                "More help:",
                f"  {bin_name} <command> <app_script.py> --help",
                f"  {bin_name} auth --help",
                f"  {bin_name} runtime --help",
            ]
        )
    )


def _load_module(path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("ara_user_app", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _format_project_name_hint(raw_error: str) -> str:
    if "project_name must match" not in raw_error:
        return raw_error
    return (
        "Invalid App project_name.\n"
        "Use DNS-safe project names with lowercase letters, digits, and hyphens only.\n"
        "Allowed pattern: [a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\n"
        "Example: my-app-1"
    )


def _discover_app(module: ModuleType) -> App:
    for _, value in vars(module).items():
        if isinstance(value, App):
            return value
    raise RuntimeError("No App(...) instance found in script")


def main() -> None:
    bin_name = pathlib.Path(sys.argv[0]).name or "ara"
    if len(sys.argv) >= 2 and sys.argv[1] in {"-h", "--help", "help"}:
        _print_help(bin_name)
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "runtime":
        if len(sys.argv) == 2:
            run_runtime_cli(argv=["--help"])
            return
        run_runtime_cli(argv=sys.argv[2:])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "auth":
        if len(sys.argv) == 2:
            run_auth_cli(argv=["--help"])
            return
        run_auth_cli(argv=sys.argv[2:])
        return
    if len(sys.argv) < 3:
        raise SystemExit(f"Usage: {bin_name} <command> <app_script.py> [args...]")
    command = sys.argv[1]
    script = pathlib.Path(sys.argv[2]).expanduser().resolve()
    if not script.exists():
        raise SystemExit(f"Script not found: {script}")
    try:
        module = _load_module(script)
    except ValueError as exc:
        hint = _format_project_name_hint(str(exc))
        if hint == str(exc):
            raise
        raise SystemExit(hint) from None
    app = _discover_app(module)
    _run_app_cli(app, argv=[command, *sys.argv[3:]], default_command=command)


if __name__ == "__main__":
    main()
