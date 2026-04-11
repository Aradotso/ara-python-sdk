# Canonical Email + Cron Example

Minimal end-to-end example for:

- chat-driven `send_email` tool calls
- recurring automations (`automation_create`, `automation_list`, `automation_update`, `automation_delete`)
- a tiny Vite frontend that calls app runtime directly

No local gateway or Python bridge server.

## Layout

```text
examples/canonical-email-chat-cron/
  app.py
  .env.example
  frontend/
    index.html
    main.js
    styles.css
    package.json
    .env.example
```

## 1) Configure and deploy app

```bash
cd ara-python-sdk/examples/canonical-email-chat-cron
cp .env.example .env.local
```

Set at least:

- `ARA_API_KEY` (or `ARA_ACCESS_TOKEN`)
- `RESEND_API_KEY`
- `CRON_EMAIL_FROM`

Deploy and create runtime setup:

```bash
python3 app.py deploy
python3 app.py setup
```

## 2) Resolve runtime auth with SDK CLI

```bash
python3 app.py setup-auth --ensure-runtime-key true
```

This keeps runtime key/bootstrap logic inside SDK CLI primitives.

## 3) Configure frontend

Create `frontend/.env.local`:

```dotenv
VITE_ARA_API_BASE_URL=https://api.ara.so
VITE_ARA_APP_ID=app_...
VITE_ARA_RUNTIME_KEY=ak_app_...
VITE_ARA_CHAT_AGENT_ID=demo-chat
```

Use values from `setup` / `setup-auth` output.

## 4) Run frontend

```bash
cd ara-python-sdk/examples/canonical-email-chat-cron/frontend
npm install
npm run dev
```

Open the Vite URL and chat.

## Runtime request shape

Frontend sends:

```json
{
  "agent_id": "demo-chat",
  "workflow_id": "demo-chat",
  "warmup": false,
  "input": {
    "message": "schedule an email every minute",
    "run_id": "web-..."
  }
}
```

to:

`POST {VITE_ARA_API_BASE_URL}/v1/apps/{VITE_ARA_APP_ID}/run`

with:

`Authorization: Bearer {VITE_ARA_RUNTIME_KEY}`

## Security note

`VITE_*` variables are exposed to the browser. This is a teaching/demo setup, not a production frontend auth pattern.
For convenience, `frontend/main.js` also stores config (including `VITE_ARA_RUNTIME_KEY`) in `localStorage`.
Any XSS on the same origin can read and exfiltrate that key.
