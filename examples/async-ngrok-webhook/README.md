# Async + ngrok Minimal Example

This example shows both async channels in one flow:

- callback channel: Ara posts run completion to your local server through ngrok
- polling channel: client polls `run-status` until the same run finishes

## Files

- `app.py`: minimal app manifest + workflow
- `webhook_receiver.py`: local callback receiver with optional signature verification
- `run_async_ngrok.py`: submits webhook-mode async run and polls status

## Prerequisites

- `ngrok` installed and authenticated (`ngrok config add-authtoken ...`)
- `ARA_API_KEY` set (from `Settings -> System -> API Key -> Copy API Key`)
- optional: `ARA_API_BASE_URL` if you are not using production

## 1) Deploy and bootstrap auth

```bash
cd ara-python-sdk/examples/async-ngrok-webhook
python3 app.py deploy
python3 app.py setup-auth
```

This writes local key files used by runtime calls:

- `.runtime-key.local` (optional fallback)
- `.app-header-key.local` (`X-Ara-App-Key`)

## 2) Start local receiver

```bash
cd ara-python-sdk/examples/async-ngrok-webhook
python3 webhook_receiver.py --port 8789 --callback-secret demo-secret
```

Use your own strong secret in non-demo usage (do not keep `demo-secret`).

## 3) Expose receiver with ngrok

```bash
ngrok http 8789
```

Keep ngrok running. `run_async_ngrok.py` automatically reads the public URL from `http://127.0.0.1:4040/api/tunnels`.

## 4) Submit async run with callback + polling

```bash
cd ara-python-sdk/examples/async-ngrok-webhook
python3 run_async_ngrok.py \
  --workflow demo-agent \
  --message "Test async callback over ngrok" \
  --callback-secret demo-secret
```

Expected behavior:

- terminal with `webhook_receiver.py` logs one callback payload on `/callback`
- `run_async_ngrok.py` prints queued/running/completed status from polling

## Optional: explicit ngrok URL

If you do not want tunnel auto-discovery:

```bash
python3 run_async_ngrok.py --ngrok-url "https://<your-id>.ngrok-free.app"
```
