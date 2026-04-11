from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

from .core import App, run_auth_cli, run_cli, run_runtime_cli


def _load_module(path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("ara_user_app", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _discover_app(module: ModuleType) -> App:
    for _, value in vars(module).items():
        if isinstance(value, App):
            return value
    raise RuntimeError("No App(...) instance found in script")


def main() -> None:
    bin_name = pathlib.Path(sys.argv[0]).name or "ara"
    if len(sys.argv) >= 2 and sys.argv[1] == "runtime":
        run_runtime_cli(argv=sys.argv[2:])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "auth":
        run_auth_cli(argv=sys.argv[2:])
        return
    if len(sys.argv) < 3:
        raise SystemExit(f"Usage: {bin_name} <command> <app_script.py> [args...]")
    command = sys.argv[1]
    script = pathlib.Path(sys.argv[2]).expanduser().resolve()
    if not script.exists():
        raise SystemExit(f"Script not found: {script}")
    module = _load_module(script)
    app = _discover_app(module)
    run_cli(app, argv=[command, *sys.argv[3:]], default_command=command)


if __name__ == "__main__":
    main()
