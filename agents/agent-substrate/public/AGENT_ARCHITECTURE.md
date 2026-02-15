# Agent Framework Runtime Architecture

This maps the conceptual diagram to implemented components in this repository.

## Completed Diagram (Mermaid)

```mermaid
flowchart TB
    U1([User Input]) --> IG[Input Guardrails\nContent / PII / Injection / Token]
    IG -->|pass| A[ReActAgent Orchestrator\nrun() / run_stream()]
    IG -->|tripwire| BLK1[[Blocked Request]]

    subgraph MEM[Memory Layer]
      direction TB
      STM[(Short-Term Memory\nRedisMemory)]
      SM[SessionManager\ncheckpoint/resume/close]
      LTM[(Long-Term Memory\nPostgresMemory)]
      STM <--> SM
      SM <--> LTM
    end

    A <--> MEM
    A --> LLM[LLM Runtime\nOpenAIClient via BaseModelClient]
    LLM --> A

    subgraph TBX[Toolbox Layer]
      direction TB
      T1[Built-in Tools\nCalculator/GetCurrentTime/WebSearch]
      T2[MCP Tools\nMCPClient + MCPTool]
      T3[WebSurferTool\nPlaywright Browser]
    end

    A -->|tool calls| TC[Tool-Call Guardrails\nallow/block + arg regex]
    TC -->|pass| TBX
    TC -->|tripwire| BLK2[[Blocked Tool Call]]
    TBX -->|tool results| A

    A --> OG[Output Guardrails\nContent / PII / LLM Judge]
    OG -->|pass| U2([User Output])
    OG -->|tripwire| BLK3[[Blocked Output]]

    OBS[(Observability\nTracing + Metrics + Logs)]
    A -. spans/metrics .-> OBS
    IG -. metrics .-> OBS
    TC -. metrics .-> OBS
    OG -. metrics .-> OBS
    MEM -. ops metrics .-> OBS
```

## Component Mapping (Diagram → Code)

- Input Guardrails
  - `src/agent_framework/guardrails/base_guardrail.py`
  - `src/agent_framework/guardrails/prebuilt.py`
  - `src/agent_framework/guardrails/runner.py`
  - Invoked in `ReActAgent.run()` / `run_stream()`

- Agent Orchestrator
  - `src/agent_framework/agents/react_agent.py`
  - Coordinates LLM calls, tool execution, memory updates, guardrail checks

- LLM
  - `src/agent_framework/model_clients/base_client.py`
  - `src/agent_framework/model_clients/openai/openai_client.py`

- Toolbox
  - Core interfaces: `src/agent_framework/tools/base_tool.py`
  - Built-ins: `src/agent_framework/tools/builtin_tools.py`
  - MCP adapter: `src/agent_framework/tools/mcp_client.py`, `src/agent_framework/tools/mcp_tool.py`
  - Browser tool: `src/agent_framework/tools/web_surfer.py`

- Memory (Short-Term + Long-Term)
  - Short-term: `src/agent_framework/memory/redis_memory.py`
  - Long-term: `src/agent_framework/memory/postgres_memory.py`
  - Orchestration: `src/agent_framework/memory/session_manager.py`
  - Serialization: `src/agent_framework/memory/message_serializer.py`

- Output Guardrails
  - Same guardrail stack as input, invoked post-generation in `react_agent.py`

- Observability (cross-cutting)
  - `src/agent_framework/observability/telemetry.py`
  - Exposed via `src/agent_framework/observability/__init__.py`

## Status Snapshot

- Implemented and wired
  - Input/output/tool-call guardrails
  - ReAct orchestration loop
  - Redis + Postgres memory tiers
  - Session lifecycle (create/resume/checkpoint/close)
  - MCP + built-in + web-surfer tools
  - OpenTelemetry metrics/traces/logging

- Known design note
  - `CalculatorTool` currently uses `eval` with stripped builtins. It works, but consider an AST-safe evaluator for hardened production environments.

## Suggested Next Extension (Optional)

- Add a `MemoryPolicy` layer (summarization + retrieval strategy) between Agent and Memory for very long sessions.
- Add per-tool rate limits/circuit breakers in the toolbox path.
- Add guardrail policy profiles per agent role (strict, standard, permissive).
