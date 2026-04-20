import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) ara auth login
# 2) ara deploy examples/00-hello-hourly.py
# 3) ara run examples/00-hello-hourly.py
# 4) ara deploy app.py --cron "*/5 * * * *" uploads/registers the job in the cloud with a 5-minute cron schedule.

@ara.tool
def utc_now() -> dict:
    from datetime import datetime, timezone
    return {"utc_time": datetime.now(timezone.utc).isoformat()}


ara.Job(
    "hello-hourly-agent",
    system_instructions=(
        "Reply with one short hello message and include UTC time. "
        "If linq_send_message is available and a phone route is paired, "
        "send the same message there once."
    ),
)
