# Framework Adapter Minimal Examples

Small, copy-pasteable examples showing the public adapter helpers in `ara_sdk`.

Included:

- `minimal_langgraph_subagent.py`
- `minimal_agno_subagent.py`

Run locally:

```bash
python3 examples/framework-adapters/minimal_langgraph_subagent.py local --input message="Need 3 slots next week"
python3 examples/framework-adapters/minimal_agno_subagent.py local --input message="Draft a follow-up reminder"
```

Deploy (when `ARA_ACCESS_TOKEN` is set):

```bash
python3 examples/framework-adapters/minimal_langgraph_subagent.py deploy
python3 examples/framework-adapters/minimal_agno_subagent.py deploy
```

These are intentionally minimal and focus on manifest shape for adapter runtime config.
