# Ara Python SDK Examples

Minimal, maintained examples for common SDK patterns.

## Quick start

```bash
cd examples
cp .env.example .env
ara deploy <example.py>
ara setup-auth <example.py> --ensure-runtime-key true
ara run <example.py> --agent <agent_id> --runtime-key "<runtime_key>" --message "hello"
```

## Example index

1. `00-get-started.py` - smallest end-to-end app.
2. `01-a-agent-skills-loading.py` - basic skill loading pattern.
3. `01-b-agent-skills-loading.py` - runtime file-backed skill loading.
4. `01-c-agent-skills-loading.py` - custom decorator-based local dispatch example.
5. `02-canonical-email-chat-cron.py` - canonical email + chat + cron workflow.
6. `03-async-ngrok-webhook.py` - async run + webhook callback (with ngrok helpers).
7. `04-calcom-booking.py` - booking assistant example.
8. `06-programmatic-secrets-redeploy.py` - programmatic secrets + redeploy flow.
9. `07-app-schedule-decorator.py` - static scheduling via `@app.schedule(...)`.
10. `07b-app-schedule-decorator.py` - runtime-created schedule (fire-and-forget).
11. `07c-runtime-automation-manager.py` - create/list/delete automation lifecycle.
12. `08-11labs-voice-reminders.py` - 11labs outbound call + recurring cron reminder flow.
13. `09-runtime-model-selector.py` - minimal `runtime(model=...)` selector example.

For full docs and walkthroughs: <https://docs.ara.so/examples/overview>
