from __future__ import annotations

from ara_sdk import App

app = App("schedule-decorator-demo")


@app.schedule(cron="0 * * * *")
@app.tool()
def cleanup_cache(path: str = "/tmp/cache") -> dict:
    """Deterministic cleanup helper for scheduled maintenance."""
    target = str(path or "").strip() or "/tmp/cache"
    return {
        "ok": True,
        "path": target,
        "note": "Demo tool only. Replace with real cleanup logic for production.",
    }


# "at" accepts:
# - HH:MM wall-clock values interpreted in timezone= (UTC by default)
# - ISO timestamps as one-shot absolute times (recommended with trailing Z)
@app.schedule(at=["09:00", "13:00", "17:30", "2026-04-11T16:45:00Z"], timezone="UTC")
@app.agent(
    entrypoint=True,
    skills=["cleanup_cache", "automation_create", "automation_list", "automation_delete"],
)
def ops_scheduler_agent(input: dict) -> str:
    """Run planned checks and manage schedules dynamically when requested."""
    payload = input if isinstance(input, dict) else {}
    message = str(payload.get("message") or "").strip()
    context = f" Context: {message}" if message else ""
    return (
        "Handle operations checks and use tools when needed."
        " If the user asks to add a new recurring job, call automation_create with schedule_kind='cron'."
        " If the user asks to remove one, call automation_list first, then automation_delete by name."
        " Use cleanup_cache for deterministic cache maintenance."
        f"{context}"
    )


# You can combine cron + at in one decorator.
@app.schedule(
    cron="0 9 * * 1-5",
    at=["12:30"],
    timezone="America/New_York",  # IANA tz database format (Area/Location)
)
@app.tool()
def weekday_digest_ping() -> dict:
    """Demo job that runs on both cron and fixed "at" trigger(s)."""
    return {
        "ok": True,
        "job": "weekday-digest-ping",
    }
