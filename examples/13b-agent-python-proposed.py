import ara_sdk as ara

# File contract for this proposal:
# - One job definition per file (selected by path in app.ara.so)
# - Optional entrypoint script is repo-relative
# - entrypoint must live in the same directory (or a child directory) as this file
# - Ara snapshots both file contents at pinned commit SHA for deterministic runs


@ara.tool
def title_case(text: str) -> str:
    """
    Convert text into title case.

    Args:
        text: Input phrase to normalize for human-friendly display.
    """
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split())


@ara.tool
def send_email(to: str, subject: str, body: str) -> dict:
    sender = ara.secret("CRON_EMAIL_FROM")
    api_key = ara.secret("RESEND_API_KEY")

    payload = {
        "from": sender,
        "to": str(to or "").strip(),
        "subject": str(subject or "").strip() or "(no subject)",
        "text": str(body or ""),
    }
    # Provider call handled by runtime integration.
    _ = (payload, api_key)
    return {"ok": True, "from": sender}


SYSTEM_INSTRUCTIONS = """
You are my weekday planning agent.
Create a concise plan with top tasks, blockers, and one focus recommendation.
"""


ara.Job(
    "weekday-priority-agent",
    system_instructions=SYSTEM_INSTRUCTIONS,
    entrypoint="./myentrypointfile.sh",
)


# No schedule in code.
# Cron/at/timezone are configured in app.ara.so job UI.

# No explicit run call.
# Runtime chooses behavior from execution context:
# - local developer run: executes the selected job in Ara sandbox/dev runtime
# - Ara cloud/import context: registers and runs using the configured job target


# Optional advanced knobs (only when needed):
#
# ara.Job(
#     "weekday-priority-agent",
#     system_instructions="...",
#     entrypoint="./scripts/myentrypointfile.sh",  # same-dir or child-dir only
#     execution={
#         "retries": {"max_attempts": 3, "backoff_seconds": 30},
#         "queue": {"concurrency_key": "weekday-priority-agent", "limit": 1},
#     },
# )


# #### DESIGN PROPOSAL
# 1) Code-first ownership, UI-first scheduling
#    - Code owns agent identity, system behavior, and tool contracts.
#    - app.ara.so UI owns cron/at/timezone and on/off lifecycle controls.
#    - This avoids config drift from splitting schedule logic across code + UI.
#
# 2) Minimal required surface area
#    - Keep required args as small as possible for "freshman mode":
#      Job(id, ...).
#    - Everything else is optional helper metadata.
#
# 3) Recommended helper args (optional, not required)
#    - system_instructions: stronger, more stable agent behavior.
#    - entrypoint: one advanced escape hatch for custom setup/install steps.
#    - execution/retries/queue: only when the default behavior is insufficient.
#
# 4) Tool definition model
#    - Tools are regular Python functions (lowest-friction default).
#    - @ara.tool registers a function into the job runtime tool surface.
#    - Function signature + type hints define the tool parameter schema.
#    - Tool docstrings are optional; they improve model routing and reliability.
#    - "Args:" sections are optional; recommended for better per-parameter context.
#
# 5) Visual schema-quality contrast
#    - title_case uses @ara.tool + rich docstring/Args for high-quality metadata.
#    - send_email is intentionally minimal to show it still works with less metadata.
#
# 6) Secrets and API-dependent scripts
#    - Keep credentials in app secrets; consume them via ara.secret("KEY").
#    - Do not hardcode API keys or provider tokens in source.
#    - Recommended upload path: CLI-managed secrets before deploy/run.
#
# 7) Runtime/dependency strategy
#    - Prefer declarative defaults first.
#    - Use entrypoint only for advanced bootstrapping (pip/uv install, system deps).
#    - Commit the entrypoint script so runs are reproducible.
#
# 8) Deterministic execution contract
#    - Job file + entrypoint are snapshotted from a pinned commit SHA.
#    - Relative entrypoint paths are constrained to this file's directory subtree.
#
# 9) Local/cloud parity
#    - No explicit run() call required in the file.
#    - Runtime behavior is selected from execution context (local dev vs cloud import).
#
# 10) Progressive disclosure
#    - Start with minimal defaults.
#    - Add helper args only when needed ("senior mode"), without changing core shape.