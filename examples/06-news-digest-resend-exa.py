import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) export EXA_API_KEY="..."
# 2) export RESEND_API_KEY="..."
# 3) export CRON_EMAIL_FROM="alerts@yourdomain.com"
# 4) export DIGEST_TO="you@yourdomain.com"
# 5) ara auth login
# 6) ara deploy examples/06-news-digest-resend-exa.py
# 7) ara run examples/06-news-digest-resend-exa.py --agent morning-news-digest-agent-v3 --input-json '{"trigger":"manual"}'
# 8) For testing, in app.ara.so set cron to: * * * * * (every minute) and enable it.
# 9) For normal use, switch cron to: 0 9 * * * (every morning at 09:00).


@ara.tool
def fetch_news_digest() -> dict:
    import subprocess
    import sys
    import time

    exa_api_key = ara.secret("EXA_API_KEY")
    topic = ara.env("DIGEST_TOPIC", default="AI agents, startups, and developer tools")

    try:
        # Docs-aligned path: use Exa official SDK client.
        from exa_py import Exa
    except ImportError:
        # Fallback: install in-sandbox if startup hooks are unavailable.
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
    data = None
    last_error = ""
    for attempt in range(3):
        try:
            data = exa.search(
                f"latest updates about {topic}",
                type="auto",
                num_results=5,
                contents={"highlights": {"maxCharacters": 500}},
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if attempt >= 2:
                return {"ok": False, "error": last_error}
            time.sleep(1.0 + attempt)

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
    sender = ara.secret("CRON_EMAIL_FROM")
    recipient = ara.secret("DIGEST_TO")

    payload = {
        "from": sender,
        "to": [recipient],
        "subject": str(subject or "").strip() or "Morning news digest",
        "text": str(body or "").strip(),
    }

    def _post_json(url: str, payload: dict, headers: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}

    try:
        data = _post_json(
            "https://api.resend.com/emails",
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {resend_api_key}",
                "User-Agent": "ara-sdk-examples/06-news-digest",
            },
        )
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"resend_http_{exc.code}", "details": details[:800]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "email_id": data.get("id"), "to": recipient}


ara.Automation(
    "morning-news-digest-agent-v3",
    system_instructions=(
        "Every morning, use fetch_news_digest to gather 5 relevant updates. "
        "Then use send_news_digest to email a concise digest."
    ),
    tools=[fetch_news_digest, send_news_digest],
)
