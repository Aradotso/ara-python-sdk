from __future__ import annotations

import argparse
import json
import os
from typing import Any

from ara_sdk import AraRuntimeClient

# Single-file automation definition. Treat this as the source of truth.
AUTOMATION_SPEC: dict[str, Any] = {
    "name": "single-file-daily-summary",
    "schedule": {
        "kind": "cron",
        "cron": "0 9 * * *",
        "timezone": "UTC",
    },
    "payload": {
        "kind": "agent_turn",
        "message": "Send my daily summary and top priorities.",
        "deliver": False,
    },
    "execution_mode": "sandbox_required",
    "misfire_policy": "fire_latest_only",
    "max_retries": 3,
    "retry_backoff_seconds": 30,
}


def _first_job_by_name(client: AraRuntimeClient, name: str) -> dict[str, Any] | None:
    rows = client.automation_list_jobs().get("jobs") or []
    for row in rows:
        if isinstance(row, dict) and str(row.get("name") or "").strip() == name:
            return row
    return None


def _schedule_matches(existing: dict[str, Any], spec: dict[str, Any]) -> bool:
    schedule = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    kind = str(schedule.get("kind") or "").strip().lower()
    if str(existing.get("schedule_kind") or "").strip().lower() != kind:
        return False
    if str(existing.get("timezone") or "UTC").strip() != str(schedule.get("timezone") or "UTC").strip():
        return False
    if kind == "cron":
        return str(existing.get("schedule_expr") or "").strip() == str(schedule.get("cron") or "").strip()
    if kind == "every":
        return int(existing.get("every_seconds") or 0) == int(schedule.get("every_seconds") or 0)
    return False


def apply_single_file_automation(client: AraRuntimeClient, spec: dict[str, Any]) -> dict[str, Any]:
    name = str(spec.get("name") or "").strip()
    if not name:
        raise RuntimeError("AUTOMATION_SPEC.name is required")

    schedule = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    kind = str(schedule.get("kind") or "").strip().lower()
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    if not payload:
        raise RuntimeError("AUTOMATION_SPEC.payload must be a non-empty object")

    existing = _first_job_by_name(client, name)
    if existing and not _schedule_matches(existing, spec):
        # Schedule changes are represented as replace (delete + create).
        client.automation_delete_job(job_id=str(existing.get("id") or ""))
        existing = None

    if existing:
        result = client.automation_update_job(
            job_id=str(existing.get("id") or ""),
            enabled=True,
            payload=payload,
            max_retries=int(spec.get("max_retries") or 3),
            retry_backoff_seconds=int(spec.get("retry_backoff_seconds") or 30),
        )
        return {
            "ok": True,
            "action": "updated",
            "job_id": existing.get("id"),
            "result": result,
        }

    result = client.automation_create_job(
        name=name,
        schedule_kind=kind,
        timezone=str(schedule.get("timezone") or "UTC"),
        every_seconds=schedule.get("every_seconds"),
        schedule_expr=str(schedule.get("cron") or ""),
        payload=payload,
        execution_mode=str(spec.get("execution_mode") or "sandbox_required"),
        misfire_policy=str(spec.get("misfire_policy") or "fire_latest_only"),
        max_retries=int(spec.get("max_retries") or 3),
        retry_backoff_seconds=int(spec.get("retry_backoff_seconds") or 30),
    )
    created = result.get("job") if isinstance(result, dict) else {}
    return {
        "ok": True,
        "action": "created",
        "job_id": (created or {}).get("id"),
        "result": result,
    }


def delete_single_file_automation(client: AraRuntimeClient, spec: dict[str, Any]) -> dict[str, Any]:
    name = str(spec.get("name") or "").strip()
    if not name:
        raise RuntimeError("AUTOMATION_SPEC.name is required")
    existing = _first_job_by_name(client, name)
    if not existing:
        return {"ok": True, "action": "noop", "reason": "job_not_found", "name": name}
    result = client.automation_delete_job(job_id=str(existing.get("id") or ""))
    return {"ok": True, "action": "deleted", "job_id": existing.get("id"), "result": result}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manage one automation from a single script. "
            "Uses ARA_API_KEY/ARA_API_BASE_URL from env or `ara auth login` credentials."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("apply", help="Create or update the automation defined in this file.")
    sub.add_parser("delete", help="Delete the automation defined in this file.")
    sub.add_parser("print-spec", help="Print the in-file automation JSON spec.")
    args = parser.parse_args()

    if args.command == "print-spec":
        print(json.dumps(AUTOMATION_SPEC, indent=2))
        return

    client = AraRuntimeClient.from_env(cwd=os.getcwd())
    if args.command == "apply":
        print(json.dumps(apply_single_file_automation(client, AUTOMATION_SPEC), indent=2))
        return
    if args.command == "delete":
        print(json.dumps(delete_single_file_automation(client, AUTOMATION_SPEC), indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
