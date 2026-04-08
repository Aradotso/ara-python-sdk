from ara_sdk import App, cron, run_cli, sandbox

app = App(
    "Meeting Booker",
    project_name="meeting-booker",
    description="Optional Cal.com-backed meeting booking flow.",
)


@app.subagent(
    id="booking-coordinator",
    workflow_id="booking-coordinator",
    handoff_to=["calendar-strategist"],
    sandbox=sandbox(max_concurrency=3),
)
def booking_coordinator(event=None):
    """Coordinate scheduling and booking actions."""


@app.hook(
    id="daily-followups",
    event="scheduler.followups",
    schedule=cron("0 13 * * 1-5"),
    agent="booking-coordinator",
)
def daily_followups():
    """Send reminders for pending confirmations."""


if __name__ == "__main__":
    run_cli(app)
