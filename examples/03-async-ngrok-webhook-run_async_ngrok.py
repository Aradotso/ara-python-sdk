#!/usr/bin/env python3
import argparse
import importlib.util
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ara_sdk import AraClient


def _load_demo_app():
    app_path = Path(__file__).with_name("03-async-ngrok-webhook.py")
    spec = importlib.util.spec_from_file_location("async_ngrok_webhook_app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load app module: {app_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    demo_app = getattr(module, "app", None)
    if demo_app is None:
        raise RuntimeError(f"Loaded module missing 'app': {app_path}")
    return demo_app


def _parse_pairs(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value
    return out


def _resolve_ngrok_https_url() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError("ngrok admin API not reachable. Start ngrok first with: ngrok http 8789") from exc
    tunnels = data.get("tunnels") or []
    for tunnel in tunnels:
        if tunnel.get("proto") == "https":
            url = str(tunnel.get("public_url") or "").strip()
            if url:
                return url
    raise RuntimeError("No https ngrok tunnel found. Start one with: ngrok http 8789")


def _extract_run_id(response: dict[str, Any]) -> str:
    run = response.get("run") if isinstance(response, dict) else None
    if isinstance(run, dict):
        rid = str(run.get("run_id") or "").strip()
        if rid:
            return rid
    rid = str(response.get("run_id") or "").strip() if isinstance(response, dict) else ""
    if rid:
        return rid
    raise RuntimeError(f"Could not extract run_id from response: {response}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit async Ara run with webhook callback over ngrok, then poll status.")
    parser.add_argument("--workflow", default="demo-agent")
    parser.add_argument("--message", default="Hello from async ngrok webhook example.")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--callback-path", default="/callback")
    parser.add_argument("--callback-secret", default="")
    parser.add_argument("--ngrok-url", default="")
    parser.add_argument("--poll-timeout", type=int, default=180)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--setup-auth", action="store_true")
    parser.add_argument("--runtime-key", default="")
    parser.add_argument("--app-header-key", default="")
    args = parser.parse_args()

    demo_app = _load_demo_app()
    client = AraClient.from_env(manifest=demo_app.manifest, cwd=str(Path(__file__).parent))

    if args.setup_auth:
        setup = client.setup_auth()
        print(json.dumps({"setup_auth": setup}, indent=2))

    ngrok_base = args.ngrok_url.strip() or _resolve_ngrok_https_url()
    callback_url = ngrok_base.rstrip("/") + args.callback_path
    input_payload: dict[str, Any] = {"message": args.message}
    input_payload.update(_parse_pairs(list(args.input)))

    callback: dict[str, Any] = {
        "url": callback_url,
        "events": ["run.completed", "run.failed"],
    }
    if args.callback_secret:
        callback["secret"] = args.callback_secret

    submit = client.run_async(
        agent_id=args.workflow,
        input_payload=input_payload,
        response_mode="webhook",
        callback=callback,
        runtime_key=(args.runtime_key or None),
        app_header_key=(args.app_header_key or None),
    )
    print(json.dumps({"submit": submit, "callback_url": callback_url}, indent=2))

    run_id = _extract_run_id(submit)
    deadline = time.time() + max(1, args.poll_timeout)
    while True:
        status = client.run_status(
            run_id=run_id,
            runtime_key=(args.runtime_key or None),
            app_header_key=(args.app_header_key or None),
        )
        run = status.get("run") if isinstance(status, dict) else {}
        state = str((run or {}).get("status") or "").strip()
        print(json.dumps({"run_id": run_id, "status": state, "run": run}, indent=2))
        if state in {"completed", "failed", "cancelled"}:
            break
        if time.time() >= deadline:
            raise TimeoutError(f"Timed out waiting for run {run_id} to finish")
        time.sleep(max(0.2, float(args.poll_interval)))


if __name__ == "__main__":
    main()
