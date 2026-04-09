#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _verify_signature(secret: str, timestamp: str, delivery_id: str, body: bytes, header_value: str) -> tuple[bool, str]:
    if not secret:
        return True, "skipped"
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False, "invalid timestamp"
    if abs(time.time() - ts) > 300:
        return False, "timestamp outside 5-minute window"
    if not header_value.startswith("sha256="):
        return False, "missing sha256= prefix"
    actual = header_value.split("=", 1)[1]
    try:
        body_text = body.decode("utf-8")
    except UnicodeDecodeError:
        return False, "invalid utf-8 body"
    signing_payload = f"{timestamp}.{delivery_id}.{body_text}"
    expected = hmac.new(
        secret.encode("utf-8"),
        signing_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if hmac.compare_digest(actual, expected):
        return True, "ok"
    return False, "signature mismatch"


class CallbackHandler(BaseHTTPRequestHandler):
    callback_secret = ""
    out_path: Path | None = None

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "invalid_content_length"}).encode("utf-8"))
            return
        if length < 0:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "invalid_content_length"}).encode("utf-8"))
            return
        raw_body = self.rfile.read(length)

        try:
            body = json.loads(raw_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "invalid_json"}).encode("utf-8"))
            return

        timestamp = self.headers.get("X-Ara-Timestamp", "")
        delivery_id = self.headers.get("X-Ara-Delivery-Id", "")
        signature = self.headers.get("X-Ara-Signature", "")
        verified, verify_message = _verify_signature(
            self.callback_secret,
            timestamp,
            delivery_id,
            raw_body,
            signature,
        )

        payload = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "signature_verified": verified,
            "signature_message": verify_message,
            "body": body,
        }

        print(json.dumps(payload, indent=2), flush=True)
        if self.out_path:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            self.out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        if not verified:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": verify_message}).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "signature_verified": verified}).encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Local webhook receiver for Ara async run callbacks.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--callback-secret", default="")
    parser.add_argument("--out", default=".last-callback.json")
    args = parser.parse_args()

    CallbackHandler.callback_secret = str(args.callback_secret or "")
    CallbackHandler.out_path = Path(args.out).resolve() if args.out else None

    server = ThreadingHTTPServer((str(args.host), int(args.port)), CallbackHandler)
    print(f"Listening on http://{args.host}:{args.port}/callback", flush=True)
    if CallbackHandler.callback_secret:
        print("Signature verification: enabled", flush=True)
    else:
        print("Signature verification: disabled (no --callback-secret)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
