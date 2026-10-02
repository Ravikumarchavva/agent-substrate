# Examples

Each file is one idea, short enough to read in a minute, and runs as-is:

```bash
uv run python examples/01_quickstart.py
```

With no `OPENAI_API_KEY` they run against a scripted model that replays canned replies, so everything works offline.
Set the key (and drop `SUBSTRATE_EXAMPLES_OFFLINE`) to talk to a real model. Each run keeps its state in `./.substrate`.

| | |
|---|---|
| [01_quickstart](01_quickstart.py) | An agent, a runtime, one answer. |
| [02_tools](02_tools.py) | Write a tool; declare its `risk` and `idempotent`; read what the run journaled. |
| [03_conversations](03_conversations.py) | Threads in a `Store` that survive a restart. |
| [04_human_approval](04_human_approval.py) | A risky tool pauses the run, durably, until a person decides. |
| [05_multi_agent](05_multi_agent.py) | An orchestrator that delegates, and flows with no model in the middle. |
| [06_guardrails](06_guardrails.py) | Middleware around turns, model calls and tool calls; write your own. |
| [07_mcp_tools](07_mcp_tools.py) | Any MCP server as tools. |
| [08_serve_http](08_serve_http.py) | One endpoint on your own FastAPI app, streaming the run as SSE. |
| [09_evals](09_evals.py) | Score an agent against a dataset with a judge model. |

`_model.py` holds `pick_model` and `ScriptedModel`. `ScriptedModel` is also the smallest possible `ChatModel` — the engine
needs only `model`, `capabilities`, `generate`/`generate_stream` and `count_tokens` — so it doubles as the answer to
"how do I plug in my own provider": write that class and pass it as `model=`.

`tests/test_examples.py` runs every example on each build.
