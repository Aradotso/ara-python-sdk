from ara_sdk import App, invoke, sandbox, schedule

app = App(
    "meeting-booker",
)


@app.agent(
    entrypoint=True,
    handoff_to=["calendar-strategist"],
    schedules=[
        schedule.cron(
            id="daily-followups",
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
def booking_coordinator(payload: dict) -> str:
    input_payload = payload if isinstance(payload, dict) else {}
    action = str(input_payload.get("action") or "").strip().lower()
    if action == "send-reminders":
        return "Send reminders for pending booking confirmations."
    return "Coordinate scheduling and booking actions."
