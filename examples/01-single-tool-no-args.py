import datetime

import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) ara auth login
# 2) ara deploy examples/01-single-tool-no-args.py
# 3) ara run examples/01-single-tool-no-args.py
# 4) Optional schedule in app.ara.so: 0 * * * * (every hour).


@ara.tool
def hello_world() -> dict:
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {"ok": True, "message": "hello world", "utc_time": now_utc}


ara.Job(
    "hello-tool-agent",
    system_instructions="Use the hello_world tool when useful, then reply briefly.",
)
