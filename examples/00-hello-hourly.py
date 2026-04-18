import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) ara auth login
# 2) ara deploy examples/00-hello-hourly.py
# 3) ara run examples/00-hello-hourly.py --agent hello-hourly-agent --input-json '{"trigger":"manual"}'
# 4) In app.ara.so, set schedule cron to: 0 * * * * (every hour) and enable it.

@ara.tool
def utc_now() -> dict:
    from datetime import datetime, timezone
    return {"utc_time": datetime.now(timezone.utc).isoformat()}


ara.Automation(
    "hello-hourly-agent",
    system_instructions=(
        "Reply with one short hello message and include UTC time. "
        "If linq_send_message is available and a phone route is paired, "
        "send the same message there once."
    ),
    tools=[utc_now],
)
