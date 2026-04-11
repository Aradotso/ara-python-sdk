# Ara Python SDK Examples

Primary examples are flat entry files in this directory and ordered for quick scanning.
Support assets live under `assets/` and frontend files under `frontend/`.

1. `00-get-started.py`
2. `01-a-agent-skills-loading.py`
3. `01-b-agent-skills-loading.py`
4. `01-c-agent-skills-loading.py`
5. `02-canonical-email-chat-cron.py`
6. `03-async-ngrok-webhook.py`
7. `04-calcom-booking.py`
8. `05-a-framework-adapters-langgraph.py`
9. `05-b-framework-adapters-agno.py`
10. `06-programmatic-secrets-redeploy.py`

## 00 - Get Started

```bash
cd examples
ara deploy 00-get-started.py
ara setup-auth 00-get-started.py --ensure-runtime-key true
# Use runtime_key returned by setup-auth output.
ara run 00-get-started.py --agent hello-agent --runtime-key "<runtime_key>" --message "Say hello to Ara"
```

## 01 - Agent Skills Loading (A/B/C)

- `01-a`: inline task instructions only
- `01-b`: uploads and executes a runtime file (`assets/01-b-agent-skills-loading-title_case.py`)
- `01-c`: decorator-backed local tool dispatch

```bash
cd examples
ara deploy 01-a-agent-skills-loading.py
ara setup-auth 01-a-agent-skills-loading.py --ensure-runtime-key true
ara run 01-a-agent-skills-loading.py --agent title-case-inline-instructions-agent --runtime-key "<runtime_key>" --message "hello from ara sdk"

ara deploy 01-b-agent-skills-loading.py
ara setup-auth 01-b-agent-skills-loading.py --ensure-runtime-key true
ara run 01-b-agent-skills-loading.py --agent title-case-runtime-file-agent --runtime-key "<runtime_key>" --message "hello from ara sdk"

ara deploy 01-c-agent-skills-loading.py
ara setup-auth 01-c-agent-skills-loading.py --ensure-runtime-key true
ara run 01-c-agent-skills-loading.py --agent title-case-decorator-agent --runtime-key "<runtime_key>" --message "hello from ara sdk"
```

## 02 - Canonical Email + Cron

Backend app + small Vite frontend in `frontend/02-canonical-email-chat-cron/`.

```bash
cd examples
cp .env.example .env.local
ara deploy 02-canonical-email-chat-cron.py
ara setup 02-canonical-email-chat-cron.py
ara setup-auth 02-canonical-email-chat-cron.py --ensure-runtime-key true
```

Frontend:

```bash
cd examples
# Reuse the same .env.local created above and fill in VITE_ARA_APP_ID + VITE_ARA_RUNTIME_KEY.
# API base is fixed in the frontend code to https://api.ara.so.
npm install
npm run dev:canonical-email-chat-cron
```

## 03 - Async + ngrok Webhook

```bash
cd examples
ara deploy 03-async-ngrok-webhook.py
ara setup-auth 03-async-ngrok-webhook.py
python3 03-async-ngrok-webhook-webhook_receiver.py --port 8789 --callback-secret demo-secret
ngrok http 8789
python3 03-async-ngrok-webhook-run_async_ngrok.py --callback-secret demo-secret
```

## 04 - Cal.com Booking

```bash
cd examples
ara deploy 04-calcom-booking.py
ara setup-auth 04-calcom-booking.py --ensure-runtime-key true
# Use runtime_key returned by setup-auth output.
ara run 04-calcom-booking.py --agent booking-coordinator --runtime-key "<runtime_key>" --input action="send-reminders"
```

## 05 - Framework Adapters

```bash
cd examples
ara deploy 05-a-framework-adapters-langgraph.py
ara setup-auth 05-a-framework-adapters-langgraph.py --ensure-runtime-key true
ara run 05-a-framework-adapters-langgraph.py --agent message-router --runtime-key "<runtime_key>" --message "Need 3 slots next week"

ara deploy 05-b-framework-adapters-agno.py
ara setup-auth 05-b-framework-adapters-agno.py --ensure-runtime-key true
ara run 05-b-framework-adapters-agno.py --agent followup-writer --runtime-key "<runtime_key>" --message "Draft a follow-up reminder"
```

## 06 - Programmatic Secrets Redeploy Probe

```bash
cd examples
python3 06-programmatic-secrets-redeploy-test.py
```
