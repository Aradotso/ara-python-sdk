# Framework Adapter Minimal Examples

Small, copy-pasteable examples showing the public adapter helpers in `ara_sdk`.

Included:

- `minimal_langgraph_subagent.py`
- `minimal_agno_subagent.py`

Run locally:

```bash
ara local examples/framework-adapters/minimal_langgraph_subagent.py --input message="Need 3 slots next week"
ara local examples/framework-adapters/minimal_agno_subagent.py --input message="Draft a follow-up reminder"
```

Deploy (when `ARA_API_KEY` is set):

```bash
ara deploy examples/framework-adapters/minimal_langgraph_subagent.py
ara deploy examples/framework-adapters/minimal_agno_subagent.py
```

These are intentionally minimal and focus on manifest shape for adapter runtime config.
