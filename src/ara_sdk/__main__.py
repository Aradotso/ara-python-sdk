from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

from .core import (
    _pop_minimal_app,
    _run_app_cli,
    run_auth_cli,
)


def _print_help(bin_name: str) -> None:
    print(
        "\n".join(
            [
                f"Usage: {bin_name} <command> <automation_script.py> [args...]",
                f"       {bin_name} <global-command> [args...]",
                "",
                "Automation commands (require <automation_script.py>):",
                "  deploy, up, run, logs",
                "",
                "Global command groups (no app script required):",
                "  auth      login/whoami/logout/rotate for CLI auth",
                "",
                "Examples:",
                f"  {bin_name} deploy app.py",
                f"  {bin_name} run app.py --agent booking_coordinator --input-json '{{\"trigger\":\"manual\"}}'",
                f"  {bin_name} auth login",
                "",
                "More help:",
                f"  {bin_name} <command> <automation_script.py> --help",
                f"  {bin_name} auth --help",
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


def _discover_app(module: ModuleType):
    _ = module
    minimal_app = _pop_minimal_app()
    if minimal_app is not None and getattr(minimal_app, "_agents", None):
        return minimal_app
    raise RuntimeError("No ara.Automation(...) declaration found in script")


def main() -> None:
    bin_name = pathlib.Path(sys.argv[0]).name or "ara"
    if len(sys.argv) >= 2 and sys.argv[1] in {"-h", "--help", "help"}:
        _print_help(bin_name)
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "auth":
        if len(sys.argv) == 2:
            run_auth_cli(argv=["--help"])
            return
        run_auth_cli(argv=sys.argv[2:])
        return
    if len(sys.argv) < 3:
        raise SystemExit(f"Usage: {bin_name} <command> <automation_script.py> [args...]")
    command = sys.argv[1]
    script = pathlib.Path(sys.argv[2]).expanduser().resolve()
    if not script.exists():
        raise SystemExit(f"Script not found: {script}")
    module = _load_module(script)
    app = _discover_app(module)
    _run_app_cli(app, argv=[command, *sys.argv[3:]], default_command=command)


if __name__ == "__main__":
    main()
