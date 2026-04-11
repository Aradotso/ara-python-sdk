# Programmatic Secrets Redeploy Probe

This example validates one behavior end-to-end:

- redeploy reconciliation keeps only currently declared `runtime(secrets=[...])` refs
- generated secret names from `Secret.from_dotenv()` / `Secret.from_dict({...})` stay stable across value rotation (same key set)

## Prerequisites

- `ARA_API_KEY` exported
- `ARA_API_BASE_URL` optional (defaults to production API)

## Run

From this folder:

```bash
python test_redeploy_reconcile.py
```

Project name is defined directly in `build_app()` inside `app.py`.

The script:

1. deploys an app with `Secret.from_dotenv(...)` and `Secret.from_dict({...})`
2. injects a stale secret (`stale-secret`)
3. redeploys with changed values but identical key names
4. asserts stale secret is removed and final remote secrets exactly match runtime secret refs

It prints a JSON summary on success.
