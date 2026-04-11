#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

for parent in pathlib.Path(__file__).resolve().parents:
    src_dir = parent / "src"
    if (src_dir / "ara_sdk").exists():
        sys.path.insert(0, str(src_dir))
        break

from ara_sdk import AraClient


def _load_build_app():
    module_path = pathlib.Path(__file__).with_name("06-programmatic-secrets-redeploy.py")
    spec = importlib.util.spec_from_file_location("programmatic_secrets_redeploy_example", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load example module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build_app = getattr(module, "build_app", None)
    if build_app is None:
        raise RuntimeError(f"Loaded module missing build_app(): {module_path}")
    return build_app


def _secret_names(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name:
            out.append(name)
    return sorted(set(out))


def _expected_secret_names(manifest: dict) -> list[str]:
    refs = ((manifest.get("runtime_profile") or {}).get("secret_refs") or [])
    out: list[str] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        name = str(ref.get("name") or "").strip()
        if name:
            out.append(name)
    return sorted(set(out))


def _write_dotenv(path: pathlib.Path, *, openai: str, anthropic: str) -> None:
    path.write_text(
        f"OPENAI_API_KEY={openai}\nANTHROPIC_API_KEY={anthropic}\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live integration probe for programmatic secret redeploy reconciliation."
    )
    parser.add_argument(
        "--example-dir",
        default=str(pathlib.Path(__file__).resolve().parent),
        help="Working directory containing 06-programmatic-secrets-redeploy.py (default: this folder).",
    )
    parser.add_argument(
        "--cleanup-app-secret-name",
        default="stale-secret",
        help="Secret name to inject between deploys and verify reconciliation removes.",
    )
    args = parser.parse_args()

    build_app = _load_build_app()
    example_dir = pathlib.Path(args.example_dir).resolve()
    phase1_env = example_dir / ".probe.phase1.env"
    phase2_env = example_dir / ".probe.phase2.env"

    _write_dotenv(phase1_env, openai="sk-phase-1", anthropic="an-phase-1")
    _write_dotenv(phase2_env, openai="sk-phase-2", anthropic="an-phase-2")

    app_phase_1 = build_app(
        dotenv_file=str(phase1_env),
        local_openai_key="sk-local-phase-1",
    )
    client_phase_1 = AraClient.from_env(manifest=app_phase_1.manifest, cwd=str(example_dir))
    deploy_1 = client_phase_1.deploy()
    app_id = str(deploy_1.get("app_id") or "").strip()
    if not app_id:
        raise RuntimeError(f"deploy did not return app_id: {deploy_1}")

    expected_phase_1 = _expected_secret_names(app_phase_1.manifest)
    rows_phase_1 = client_phase_1.http.list_secrets(app_id).get("secrets") or []
    names_phase_1 = _secret_names(rows_phase_1)

    client_phase_1.http.upsert_secret(
        app_id,
        name=str(args.cleanup_app_secret_name).strip().lower(),
        values={"OLD_KEY": "legacy"},
    )
    rows_with_stale = client_phase_1.http.list_secrets(app_id).get("secrets") or []
    names_with_stale = _secret_names(rows_with_stale)

    app_phase_2 = build_app(
        dotenv_file=str(phase2_env),
        local_openai_key="sk-local-phase-2",
    )
    client_phase_2 = AraClient.from_env(manifest=app_phase_2.manifest, cwd=str(example_dir))
    deploy_2 = client_phase_2.deploy()
    if str(deploy_2.get("app_id") or "").strip() != app_id:
        raise RuntimeError(f"unexpected app_id change across redeploy: {deploy_2}")

    expected_phase_2 = _expected_secret_names(app_phase_2.manifest)
    rows_phase_2 = client_phase_2.http.list_secrets(app_id).get("secrets") or []
    names_phase_2 = _secret_names(rows_phase_2)

    if expected_phase_1 != expected_phase_2:
        raise RuntimeError(
            "Expected stable generated secret names across value rotation, "
            f"got phase1={expected_phase_1} phase2={expected_phase_2}"
        )
    cleanup_name = str(args.cleanup_app_secret_name).strip().lower()
    if cleanup_name not in names_with_stale:
        raise RuntimeError("stale secret injection failed, cannot validate reconciliation")
    if cleanup_name in names_phase_2:
        raise RuntimeError("stale secret survived redeploy reconciliation")
    if names_phase_2 != expected_phase_2:
        raise RuntimeError(
            "remote secret set after redeploy should match runtime secret_refs exactly; "
            f"expected={expected_phase_2} actual={names_phase_2}"
        )

    print(
        json.dumps(
            {
                "ok": True,
                "project_slug": str(app_phase_1.manifest.get("slug") or ""),
                "app_id": app_id,
                "expected_secret_names": expected_phase_2,
                "phase_1_secret_names": names_phase_1,
                "phase_1_plus_stale": names_with_stale,
                "phase_2_secret_names": names_phase_2,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
