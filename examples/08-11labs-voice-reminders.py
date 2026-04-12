from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from dotenv import load_dotenv
from ara_sdk import App, Secret, invoke, runtime, schedule

load_dotenv(".env")


def _labs11_request(method: str, path: str, payload: dict | None = None) -> dict:
    headers: dict[str, str] = {
        "xi-api-key": os.getenv("LABS11_API_KEY", ""),
        "User-Agent": "ara-11labs-demo/1.0",
    }
    body: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        f"https://api.elevenlabs.io{path}",
        data=body,
        method=method.upper(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw_text = response.read().decode("utf-8", errors="replace")
            if not raw_text.strip():
                return {"ok": True, "status": response.status, "json": {}}
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if "application/json" in content_type:
                return {"ok": True, "status": response.status, "json": json.loads(raw_text)}
            return {"ok": True, "status": response.status, "text": raw_text}
    except urllib.error.HTTPError as exc:
        raw_text = exc.read().decode("utf-8", errors="replace")
        if raw_text.strip():
            try:
                return {"ok": False, "status": exc.code, "error": json.loads(raw_text)}
            except json.JSONDecodeError:
                return {"ok": False, "status": exc.code, "error": raw_text}
        return {"ok": False, "status": exc.code, "error": "11labs request failed"}


def _extract_e164_from_text(raw_text: str) -> str:
    match = re.search(r"\+\d{8,15}", str(raw_text or ""))
    return str(match.group(0) if match else "").strip()


def _resolve_call_binding(agent_id: str, phone_number_id: str) -> dict:
    response = _labs11_request("GET", "/v1/convai/phone-numbers")
    if not response.get("ok"):
        return {"ok": False, "error": "failed to list 11labs phone numbers", "details": response}

    rows = response.get("json")
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "no 11labs phone numbers found"}

    selected = None
    if phone_number_id:
        for row in rows:
            if str((row or {}).get("phone_number_id") or "").strip() == phone_number_id:
                selected = row
                break

    if selected is None and agent_id:
        for row in rows:
            assigned = (row or {}).get("assigned_agent") or {}
            if str(assigned.get("agent_id") or "").strip() == agent_id:
                selected = row
                break

    if selected is None:
        for row in rows:
            assigned = (row or {}).get("assigned_agent") or {}
            if str(assigned.get("agent_id") or "").strip():
                selected = row
                break

    if selected is None:
        selected = rows[0]

    selected = selected if isinstance(selected, dict) else {}
    assigned = selected.get("assigned_agent") if isinstance(selected.get("assigned_agent"), dict) else {}

    resolved_phone_id = phone_number_id or str(selected.get("phone_number_id") or "").strip()
    resolved_agent_id = agent_id or str(assigned.get("agent_id") or "").strip()

    if not resolved_phone_id:
        return {"ok": False, "error": "resolved phone number is missing phone_number_id", "selected": selected}
    if not resolved_agent_id:
        return {
            "ok": False,
            "error": "resolved phone number has no assigned agent_id; set LABS11_AGENT_ID explicitly",
            "selected": selected,
        }

    return {
        "ok": True,
        "agent_id": resolved_agent_id,
        "agent_phone_number_id": resolved_phone_id,
        "selected_phone_number": {
            "phone_number_id": selected.get("phone_number_id"),
            "phone_number": selected.get("phone_number"),
        },
    }


app = App(
    os.getenv("ARA_11LABS_DEMO_APP_SLUG", "11labs-voice-reminder-demo"),
    runtime_profile=runtime(
        secrets=[
            Secret.from_dict(
                {
                    "LABS11_API_KEY": os.getenv("LABS11_API_KEY"),
                    "LABS11_AGENT_ID": os.getenv("LABS11_AGENT_ID"),
                    "LABS11_AGENT_PHONE_NUMBER_ID": os.getenv("LABS11_AGENT_PHONE_NUMBER_ID"),
                    "LABS11_DEFAULT_TO_NUMBER": os.getenv("LABS11_DEFAULT_TO_NUMBER"),
                    "ORAGNIZER_PHONE_NUMBER": os.getenv("ORAGNIZER_PHONE_NUMBER") or "+13412379900",
                }
            ),
        ],
    ),
)


@app.tool()
def labs11_start_outbound_call(
    to_number: str,
    agent_id: str = "",
    agent_phone_number_id: str = "",
) -> dict:
    """Start an outbound reminder call through 11labs voice agents."""
    target_number = str(to_number or "").strip()
    chosen_agent_id = str(agent_id or os.getenv("LABS11_AGENT_ID", "")).strip()
    chosen_phone_number_id = str(
        agent_phone_number_id or os.getenv("LABS11_AGENT_PHONE_NUMBER_ID", "")
    ).strip()

    if not target_number:
        return {"ok": False, "error": "missing to_number"}
    if not chosen_agent_id or not chosen_phone_number_id:
        resolved = _resolve_call_binding(chosen_agent_id, chosen_phone_number_id)
        if not resolved.get("ok"):
            return resolved
        chosen_agent_id = str(resolved.get("agent_id") or "").strip()
        chosen_phone_number_id = str(resolved.get("agent_phone_number_id") or "").strip()

    payload = {
        "agent_id": chosen_agent_id,
        "agent_phone_number_id": chosen_phone_number_id,
        "to_number": target_number,
    }
    response = _labs11_request("POST", "/v1/convai/twilio/outbound-call", payload=payload)
    if not response.get("ok"):
        return response
    return {
        "ok": True,
        "agent_id": chosen_agent_id,
        "agent_phone_number_id": chosen_phone_number_id,
        "to_number": target_number,
        "response": response.get("json"),
    }


WEEKDAY_11LABS_REMINDER = schedule.cron(
    id="weekday-11labs-voice-reminder",
    expr="*/15 * * * *",
    timezone="UTC",
    run=invoke.tool(
        "labs11_start_outbound_call",
        args={
            "to_number": (
                os.getenv("LABS11_DEFAULT_TO_NUMBER")
                or os.getenv("ORAGNIZER_PHONE_NUMBER")
                or "+13412379900"
            ),
        },
    ),
)


@app.agent(
    entrypoint=True,
    schedules=[WEEKDAY_11LABS_REMINDER],
    skills=[
        "labs11_start_outbound_call",
    ],
)
def labs11_voice_ops_assistant(payload: dict) -> str:
    input_payload = payload if isinstance(payload, dict) else {}
    raw_text_payload = str(payload or "")
    mode = str(input_payload.get("mode") or "").strip().lower()
    organizer_phone_number = str(os.getenv("ORAGNIZER_PHONE_NUMBER") or "+13412379900").strip()
    message_text = str(input_payload.get("message") or raw_text_payload).strip()
    destination_number = str(input_payload.get("to_number") or "").strip()

    if not destination_number:
        destination_number = _extract_e164_from_text(message_text)

    lowered_message = message_text.lower().strip()
    if "call me" in lowered_message:
        destination_number = organizer_phone_number

    if mode in {"call-only", "scheduled-voice-reminder"} or "call" in lowered_message:
        if not destination_number:
            return "Please provide a destination phone number in E.164 format (example: +13412379900)."
        result = labs11_start_outbound_call(to_number=destination_number)
        return json.dumps(result)

    return (
        f"You are a 11labs outbound-calling assistant. Organizer phone number is {organizer_phone_number}. "
        "When the user asks for a call, run labs11_start_outbound_call and return the tool result."
    )
