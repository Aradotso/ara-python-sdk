# Ara SDK Skill Patterns (3 Minimal Apps)

This example folder shows three ways to run the same "title case" skill behavior in Ara SDK.
Each variant is a complete, minimal, single-file app (`app.py`).

## Folder layout

- `01-inline-instructions/app.py`
  - No custom tool function. Agent task text contains inline bash/Python instructions.
- `02-script-referenced/app.py`
  - Uses Ara SDK `runtime(files=[local_file(...)])` to upload `scripts/title_case.py` and references that runtime path in task text.
- `03-decorator-handler/app.py`
  - Tool dispatches to a decorator-registered handler.

## Run any example

```bash
cd examples/agent-skills-loading/01-inline-instructions
ara local app.py --input text="hello from ara sdk"
```

You can swap `01-inline-instructions` with:

- `02-script-referenced`
- `03-decorator-handler`
