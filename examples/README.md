# Ara Python SDK Examples

Getting-started set:

- `00-hello-hourly.py`
- `01-single-tool-no-args.py`
- `02-three-tools-metadata.py`
- `04-env-and-secrets.py`
- `05-entrypoint-install.py`
- `06-news-digest-resend-exa.py`

Minimal command pattern (replace script name):

```bash
ara auth login
ara deploy examples/<script.py>
ara run examples/<script.py> --agent <agent_id> --input-json '{"trigger":"manual"}'
```
