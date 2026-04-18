#!/usr/bin/env python3
"""Deploy an Ara app script using API-key auth (no interactive login required)."""

import argparse
import os
import subprocess
import sys


def _run(cmd: list[str], env: dict[str, str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy an Ara app script with explicit API key support. "
            "Use --api-key/ARA_API_KEY for non-interactive auth, or rely on "
            "existing `ara auth login` credentials."
        )
    )
    parser.add_argument("app_script", help="Path to app script, e.g. examples/13b-agent-python-proposed.py")
    parser.add_argument(
        "--api-key",
        default="",
        help="Ara API key override. If omitted, uses ARA_API_KEY env or existing CLI login.",
    )
    parser.add_argument(
        "--api-base-url",
        default="",
        help="Optional API base URL override. If omitted, uses ARA_API_BASE_URL/default.",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    api_key = (args.api_key or env.get("ARA_API_KEY", "")).strip()
    if api_key:
        env["ARA_API_KEY"] = api_key
    if args.api_base_url.strip():
        env["ARA_API_BASE_URL"] = args.api_base_url.strip()

    _run(["ara", "deploy", args.app_script], env=env)

    print("Deploy complete.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except KeyboardInterrupt:
        raise SystemExit(130)
