import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) ara auth login
# 2) ara deploy examples/07-file-journal.py
# 3) ara run examples/07-file-journal.py
# 4) Run step 3 a few times to watch the journal grow.

SYSTEM_INSTRUCTIONS = "On each run, call append_heartbeat once, then call read_recent with limit=3. Reply with the newest timestamp and total count."


@ara.tool
def append_heartbeat() -> dict:
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    journal_path = Path("/tmp/file-journal.json")
    rows = json.loads(journal_path.read_text()) if journal_path.exists() else []
    rows.append({"utc_time": datetime.now(timezone.utc).isoformat(), "note": "heartbeat"})
    journal_path.write_text(json.dumps(rows, indent=2))
    return {"ok": True, "count": len(rows), "path": str(journal_path)}


@ara.tool
def read_recent(limit: int = 3) -> dict:
    import json
    from pathlib import Path

    journal_path = Path("/tmp/file-journal.json")
    rows = json.loads(journal_path.read_text()) if journal_path.exists() else []
    return {"ok": True, "recent": rows[-max(1, int(limit)) :], "count": len(rows)}


app = ara.Automation(
    "file-journal-agent",
    system_instructions=SYSTEM_INSTRUCTIONS,
    tools=[append_heartbeat, read_recent],
)
