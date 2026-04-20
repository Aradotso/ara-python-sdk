import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) Create .env.local in ara-python-sdk/:
#    EXA_API_KEY=...
#    RESEND_API_KEY=...
#    CRON_EMAIL_FROM=alerts@yourdomain.com   # optional
#    DIGEST_TO=you@yourdomain.com            # optional
# 2) ara auth login
# 3) ara deploy examples/06-news-digest-resend-exa.py
# 4) ara run examples/06-news-digest-resend-exa.py
# 5) For testing, in app.ara.so set cron to: * * * * * (every minute) and enable it.
# 6) For normal use, switch cron to: 0 9 * * * (every morning at 09:00).

SYSTEM_INSTRUCTIONS = (
    "On every run, call fetch_news_digest, then call send_news_digest. "
    "Do not answer until both tools have been called."
)


def _load_dotenv() -> None:
    import os
    from pathlib import Path

    for filename in (".env", ".env.local"):
        path = Path(filename)
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

@ara.tool
def fetch_news_digest() -> dict:
    import subprocess
    import sys

    exa_api_key = ara.secret("EXA_API_KEY")
    topic = ara.env("DIGEST_TOPIC", default="AI agents, startups, and developer tools")

    try:
        from exa_py import Exa
    except ImportError:
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--break-system-packages",
                    "exa-py",
                ],
                check=True,
            )
            from exa_py import Exa
        except (subprocess.CalledProcessError, ImportError) as exc:
            return {"ok": False, "error": "missing_exa_py", "details": str(exc)}

    exa = Exa(exa_api_key)
    try:
        data = exa.search(
            f"latest updates about {topic}",
            type="auto",
            num_results=5,
            contents={"highlights": {"maxCharacters": 500}},
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "exa_search_failed", "details": str(exc)}

    results = data.results if hasattr(data, "results") and isinstance(data.results, list) else []
    lines = []
    for row in results[:5]:
        title = str(getattr(row, "title", "") or "Untitled").strip()
        url = str(getattr(row, "url", "") or "").strip()
        highlights = getattr(row, "highlights", None)
        if url:
            lines.append(f"- {title} ({url})")
        else:
            lines.append(f"- {title}")
        if isinstance(highlights, list) and highlights:
            lines.append(f"  > {str(highlights[0]).strip()[:220]}")
    digest = "\n".join(lines) if lines else "- No results returned."
    return {"ok": True, "topic": topic, "digest": digest}


@ara.tool
def send_news_digest(subject: str, body: str) -> dict:
    import json
    import urllib.error
    import urllib.request

    resend_api_key = ara.secret("RESEND_API_KEY")
    sender = ara.env("CRON_EMAIL_FROM", default="alerts@yourdomain.com")
    recipient = ara.env("DIGEST_TO", default=sender)

    payload = {
        "from": sender,
        "to": [recipient],
        "subject": str(subject or "").strip() or "Morning news digest",
        "text": str(body or "").strip(),
    }

    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {resend_api_key}",
            "User-Agent": "ara-sdk-examples/06-news-digest",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"resend_http_{exc.code}", "details": details[:800]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "resend_request_failed", "details": str(exc)}

    return {"ok": True, "email_id": data.get("id"), "to": recipient}


ara.Job(
    "morning-news-digest-agent-v3",
    system_instructions=SYSTEM_INSTRUCTIONS,
)
