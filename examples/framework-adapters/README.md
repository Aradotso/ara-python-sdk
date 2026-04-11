# Framework Adapter Minimal Examples

Small, copy-pasteable examples showing the public adapter helpers in `ara_sdk`.

Included:

- `minimal_langgraph_subagent.py`
- `minimal_agno_subagent.py`

Run:

```bash
python3 examples/framework-adapters/minimal_langgraph_subagent.py deploy
python3 examples/framework-adapters/minimal_langgraph_subagent.py setup-auth
python3 examples/framework-adapters/minimal_langgraph_subagent.py run --message "Need 3 slots next week"

python3 examples/framework-adapters/minimal_agno_subagent.py deploy
python3 examples/framework-adapters/minimal_agno_subagent.py setup-auth
python3 examples/framework-adapters/minimal_agno_subagent.py run --message "Draft a follow-up reminder"
```

These are intentionally minimal and focus on manifest shape for adapter runtime config.
