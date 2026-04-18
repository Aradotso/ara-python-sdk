# Ara Python SDK Examples

Getting-started set:

- `00-hello-hourly.py`
- `01-single-tool-no-args.py`
- `02-three-tools-metadata.py`
- `04-env-and-secrets.py`
- `05-entrypoint-install.py`
- `06-news-digest-resend-exa.py`
- `07-file-journal.py`
- `08-news-board.py`
- `09-collab-studio.py`
- `10-local-visual-console.py` (local web UI for `09-collab-studio.py`)
- `11-gmail-to-imessage.py`

Minimal command pattern (replace script name):

```bash
ara auth login
ara deploy examples/<script.py>
ara run examples/<script.py>
```

Visual demo (local browser UI):

```bash
# requires EXA_API_KEY for examples/09-collab-studio.py
python examples/10-local-visual-console.py
# then open http://127.0.0.1:8787
```
