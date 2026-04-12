from __future__ import annotations

from ara_sdk import App

app = App("runtime-automation-manager-demo")


@app.agent(entrypoint=True, skills=["automation_create", "automation_list", "automation_delete"])
def create_then_list_agent(input: dict) -> str:
    """Minimal manager: create one automation job, then list jobs."""
    _ = input if isinstance(input, dict) else {}
    return """
1) Call automation_create with:
{
  "name": "runtime-demo-job",
  "schedule_kind": "cron",
  "schedule_expr": "0 9 * * *",
  "timezone": "UTC",
  "execution_kind": "agent_turn",
  "message": "Run a lightweight runtime check."
}
2) Call automation_list.
3) Return a concise summary.
""".strip()


@app.agent(skills=["automation_create", "automation_list", "automation_delete"])
def list_delete_list_agent(input: dict) -> str:
    """Minimal manager: list jobs, delete one, then list again."""
    payload = input if isinstance(input, dict) else {}
    target_name = str(payload.get("name") or "runtime-demo-job").strip()
    return (
        "1) Call automation_list.\n"
        f"2) Find the job id where name == '{target_name}'.\n"
        "3) Call automation_delete with {'job_id': '<matched-id>'}.\n"
        "4) Call automation_list again.\n"
        "5) Return before/after summary."
    )
