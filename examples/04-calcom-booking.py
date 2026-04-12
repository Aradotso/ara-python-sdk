from ara_sdk import App, invoke, sandbox, schedule

app = App("meeting-booker")


@app.agent(
    entrypoint=True,
    handoff_to=["calendar-strategist"],
    schedules=[
        schedule.cron(
            expr="0 13 * * 1-5",
            timezone="UTC",
            run=invoke.agent(
                "booking_coordinator",
                input={
                    "action": "send-reminders",
                    "target": "pending-confirmations",
                    "context": {"source": "schedule.daily-followups"},
                },
            ),
        )
    ],
    sandbox=sandbox(max_concurrency=3),
)
def booking_coordinator(input: dict) -> str:
    input_payload = input if isinstance(input, dict) else {}
    action = str(input_payload.get("action") or "").strip().lower()
    if action == "send-reminders":
        return "Send reminders for pending booking confirmations."
    return "Coordinate scheduling and booking actions."
