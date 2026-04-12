from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.error
import urllib.request

from ara_sdk import App, Secret, runtime
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent

load_dotenv(ROOT / ".env.local", override=False)


def _required_env_value(key: str) -> str:
    value = str(os.getenv(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


app = App(
    os.getenv("ARA_DEMO_APP_SLUG", "canonical-email-chat-cron-demo"),
    interfaces={
        # Keep this OFF for isolation. Enabling it lets app sessions use owner-authenticated
        # connector tools, which can unintentionally expose owner credentials to chat sessions.
        "inherit_owner_tools": str(os.getenv("ARA_DEMO_INHERIT_OWNER_TOOLS", "false")).strip().lower()
        in {"1", "true", "yes", "on"},
    },
    runtime_profile=runtime(
        secrets=[
            Secret.from_dict(
                "resend-runtime",
                {
                    "RESEND_API_KEY": _required_env_value("RESEND_API_KEY"),
                    "CRON_EMAIL_FROM": _required_env_value("CRON_EMAIL_FROM"),
                },
                required_keys=["RESEND_API_KEY", "CRON_EMAIL_FROM"],
            ),
        ],
    ),
)


def _looks_like_email(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    # Intentional minimal validation for demo ergonomics.
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate) is not None


@app.tool()
def send_email(to: str, subject: str, body: str) -> dict:
    """Send one email via Resend API."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    sender = (os.getenv("CRON_EMAIL_FROM") or "").strip()
    recipient = (to or "").strip()
    if not api_key:
        return {"ok": False, "error": "missing RESEND_API_KEY"}
    if not sender:
        return {"ok": False, "error": "missing CRON_EMAIL_FROM"}
    if not recipient:
        return {"ok": False, "error": "missing recipient"}
    if not _looks_like_email(recipient):
        return {"ok": False, "error": "invalid recipient email"}

    payload = {
        "from": sender,
        "to": [recipient],
        "subject": str(subject or "").strip() or "(no subject)",
        "text": str(body or ""),
    }
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "canonical-email-chat-cron-demo/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            return {"ok": True, "provider": "resend", "id": parsed.get("id"), "to": recipient}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "error": raw[:2000]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.agent(
    entrypoint=True,
    skills=[
        "send_email",
        "automation_create",
        "automation_list",
        "automation_update",
        "automation_delete",
    ],
)
def demo_chat(payload: dict) -> str:
    input_payload = payload if isinstance(payload, dict) else {}
    intent = str(input_payload.get("intent") or "").strip().lower()
    if intent == "schedule-recurring-email":
        return """
You are a concise assistant in an active Ara cloud session.
For recurring schedules, use automation_create/automation_list/automation_update/automation_delete.
Prefer automation_create with execution_kind='app_tool_call', tool_name='send_email',
and tool_args {'to','subject','body'}.
Only claim success if tool output confirms it.
""".strip()
    return """
You are a concise assistant in an active Ara cloud session.
Email delivery MUST use the send_email tool.
For recurring schedules, use automation_create/automation_list/automation_update/automation_delete.
For reliable recurring sends, prefer automation_create with execution_kind='app_tool_call',
tool_name='send_email', and tool_args {'to','subject','body'}.
Only claim success if tool output confirms it.
""".strip()
