import json
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# HOW TO RUN (minimal):
# 1) cd Ara-backend
# 2) railway run --environment prd -- bash -lc 'cd ../ara-python-sdk && python3 examples/10-local-visual-console.py'
# 3) open http://127.0.0.1:8787

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_KEY = ""


def run_json(command: list[str]) -> dict:
    try:
        result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        raise RuntimeError(f"command failed: {' '.join(command)}\n{details[:800]}") from exc

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raw = (result.stdout or "").strip()
        raise RuntimeError(f"command returned non-JSON output\n{raw[:800]}") from exc

HTML = """<!doctype html>
<html>
  <body style="font-family: Inter, sans-serif; max-width: 900px; margin: 40px auto;">
    <h1>Ara SDK Local Visual Console</h1>
    <p>Click once to trigger the collab automation and render output below.</p>
    <button id="run">Run automation</button>
    <pre id="out" style="white-space: pre-wrap; background: #f5f5f5; padding: 16px; border-radius: 12px;"></pre>
    <script>
      document.getElementById("run").onclick = async () => {
        document.getElementById("out").textContent = "Running...";
        const res = await fetch("/run", { method: "POST" });
        const data = await res.json();
        document.getElementById("out").textContent = data.output;
      };
    </script>
  </body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        page = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):  # noqa: N802
        if self.path != "/run":
            self.send_response(404)
            self.end_headers()
            return

        if not RUNTIME_KEY:
            body = json.dumps({"output": "Runtime key missing. Restart this script to deploy and set one."}).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            payload = run_json(
                [
                    "uv",
                    "run",
                    "ara",
                    "run",
                    "examples/09-collab-studio.py",
                    "--runtime-key",
                    RUNTIME_KEY,
                ]
            )
        except RuntimeError:
            body = json.dumps({"output": "Run failed. Check server logs for details."}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        output_text = str(((payload.get("result") or {}).get("output_text")) or "")
        body = json.dumps({"output": output_text or json.dumps(payload, indent=2)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    deploy = run_json(["uv", "run", "ara", "deploy", "examples/09-collab-studio.py"])
    RUNTIME_KEY = str(deploy.get("runtime_key") or "")
    print("open http://127.0.0.1:8787")
    HTTPServer(("127.0.0.1", 8787), Handler).serve_forever()
