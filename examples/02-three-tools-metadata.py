import datetime

import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) ara auth login
# 2) ara deploy examples/02-three-tools-metadata.py
# 3) ara run examples/02-three-tools-metadata.py --agent metadata-tools-agent --input-json '{"trigger":"manual"}'
# 4) Optional schedule in app.ara.so: 0 * * * * (every hour).


@ara.tool
def ping() -> dict:
    return {"ok": True, "tool": "ping"}


@ara.tool
def utc_now() -> dict:
    """Return current UTC timestamp."""
    return {"utc_time": datetime.datetime.now(datetime.timezone.utc).isoformat()}


@ara.tool
def make_briefing(topic: str, audience: str) -> dict:
    """
    Create a simple one-line briefing.

    Args:
        topic: Topic to summarize.
        audience: Audience for the briefing.
    """
    return {"briefing": f"{topic.strip()} update prepared for {audience.strip()}."}


ara.Automation(
    "metadata-tools-agent",
    system_instructions=(
        "Use tools to answer requests. "
        "Prefer ping/utc_now for deterministic checks and make_briefing for short summaries."
    ),
    tools=[ping, utc_now, make_briefing],
)
