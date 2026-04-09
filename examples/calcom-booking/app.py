from ara_sdk import App, invoke, run_cli, sandbox, schedule

app = App(
    "Meeting Booker",
    project_name="meeting-booker",
    description="Optional Cal.com-backed meeting booking flow.",
)


@app.agent(
    id="booking-coordinator",
    entrypoint=True,
    task="Coordinate scheduling and booking actions.",
    handoff_to=["calendar-strategist"],
    schedules=[
        schedule.cron(
            id="daily-followups",
            expr="0 13 * * 1-5",
            timezone="UTC",
            run=invoke.agent("booking-coordinator", input={"message": "Send reminders for pending confirmations."}),
        )
    ],
    sandbox=sandbox(max_concurrency=3),
)
def booking_coordinator():
    """Coordinate scheduling and booking actions."""


if __name__ == "__main__":
    run_cli(app)
