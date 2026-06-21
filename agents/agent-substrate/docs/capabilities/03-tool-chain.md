# 3 · Tool Chain

Tool Chain lets the LLM write a **single Python script** that calls multiple tools and pipes results between them — all inside the existing CodeInterpreter sandbox. Instead of the ReAct loop dispatching one tool at a time, the LLM writes the orchestration logic itself.

## Why this matters

In a normal ReAct loop: `query_db` → (wait) → (LLM processes) → `send_email` → (wait) — each hop re-enters the LLM.  
With Tool Chain: the LLM writes `data = tools.query_db(...); tools.send_email(body=data.text)` — the two calls happen inside one sandbox execution with no LLM round-trips between them.

Large data never re-enters the sandbox as text either — it is passed **by reference** (a `DataRef` pointer) store-to-tool.

## Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart LR
    classDef llm   fill:#E3F2FD,stroke:#1565C0,color:#0D47A1,font-weight:bold
    classDef l2    fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef l1    fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef infra fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2

    LLM["LLM\nwrites Python script"]:::llm

    subgraph CHAIN["ToolChainTool  (L2 — capabilities/tools/chain/)"]
        style CHAIN fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
        TCT["ToolChainTool.execute()\ntool.py"]:::l2
        PRE["build_prelude()\nprelude.py\ninjects tools namespace\nToolResult, artifacts"]:::l2
        BRIDGE["BridgeSession\nbridge.py\nper-chain token\ndispatches to ToolInvoker"]:::l2
        REG["ChainBridgeRegistry\nbridge.py\nactive sessions map"]:::l2
    end

    subgraph L1["L1 agents layer"]
        style L1 fill:#E8EAF6,stroke:#3949AB,color:#1A237E
        INV["ToolInvoker\nagents/tools/invoker.py\nrisk + approval + ctx"]:::l1
    end

    VM["CodeInterpreter\nFirecracker / K8s sandbox"]:::infra

    LLM -->|"tool_chain(code=script)"| TCT
    TCT --> PRE
    TCT --> BRIDGE
    BRIDGE --> REG
    TCT -->|"execute(prelude + user_code)"| VM
    VM -->|"tools.query_db(...)"| BRIDGE
    BRIDGE -->|"invoke(ToolCallRequest)"| INV
    INV -->|"ToolExecutionResult"| BRIDGE
    BRIDGE -->|"ToolResult handle"| VM
    VM -->|"exec result"| TCT
```

## End-to-end execution sequence

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant LLM
    participant TCT as ToolChainTool
    participant PRE as build_prelude()
    participant BRIDGE as BridgeSession
    participant INV as ToolInvoker
    participant VM as Sandbox VM

    LLM->>TCT: execute(code="data=tools.query_db(...)")

    TCT->>PRE: build_prelude(tool_names, bridge_url, chain_id, token)
    PRE-->>TCT: prelude source (stdlib only, injects tools namespace)

    TCT->>BRIDGE: BridgeSession(chain_id, invoker, token)
    TCT->>VM: execute(prelude + wrapped_user_code)

    VM->>VM: run prelude — set up tools namespace

    loop user script calls
        VM->>BRIDGE: POST /internal/chain/{id}/invoke  Bearer {token}
        Note over VM,BRIDGE: ToolCallRequest{name, arguments, call_id}
        BRIDGE->>INV: invoke(call, session, ctx)
        Note over BRIDGE,INV: risk check, approval gate if HIGH/CRITICAL
        INV-->>BRIDGE: ToolExecutionResult
        BRIDGE-->>VM: InvocationResult {text, structured, artifact_ref}
        VM->>VM: ToolResult handle — .text / .structured / .ref
    end

    VM-->>TCT: exec_result (text output + media blocks)
    TCT->>BRIDGE: deregister(chain_id)  — invalidates token
    TCT-->>LLM: ToolExecutionResult (summary + ChainRunResult)
```

## The `tools` namespace in the sandbox

`build_prelude()` injects a stdlib-only Python preamble. Inside the script the LLM can write:

```python
# Call any registered tool
data = tools.query_db(query="SELECT * FROM events LIMIT 1000")

# Pass large data by reference — payload never re-enters the sandbox as text
summary = tools.analysis_tool(data=data)   # DataRef passed by {$artifact: ref}

# Materialise to disk for pandas / numpy
path = await data.materialize()   # downloads artifact → /workspace/artifact_abc.csv
import pandas as pd
df = pd.read_csv(path)

# Upload sandbox-produced files back
chart_ref = artifacts.put("/workspace/chart.png", "image/png")

return {"summary": summary.text, "chart": chart_ref}
```

### `ToolResult` handle

| Attribute | Type | Contents |
|---|---|---|
| `.text` | `str` | Inline text or preview |
| `.structured` | `dict` | `structured_content` from `ToolExecutionResult` |
| `.ref` | `str \| None` | `DataRef` ID — present when data was offloaded to Redis/S3 |
| `.files` | `list` | Media blocks (images, files) |
| `await .materialize()` | `str` | Downloads ref to `/workspace/…` and returns local path |

### Pass-by-reference

When a `ToolResult` with a non-`None` `.ref` is passed as a tool argument, the prelude serialises it as `{"$artifact": ref}`. The bridge resolves it server-side — the actual payload travels store-to-tool without re-entering the sandbox.

## Security

| Mechanism | Where |
|---|---|
| Per-chain bearer token (32-byte random, single-use) | `BridgeSession.__init__` |
| Token invalidated in `finally` even on crash | `ToolChainTool.execute()` |
| K8s NetworkPolicy gates sandbox → engine HTTP | Deployment manifest |
| Risk / approval still enforced per tool call | `ToolInvoker.invoke()` |
| `timeout` capped at `policy.total_timeout_s` | `ToolChainTool._run_chain()` |

## Wiring

`ToolChainTool` requires an active `CodeInterpreterTool` — if `CODE_INTERPRETER_URL` is unset, the constructor raises `RuntimeError` and the tool is simply not registered.

```python
# In lifespan
tool = ToolChainTool(
    invoker=ToolInvoker(registry, approval, artifact_store, policy),
    interpreter=code_interpreter_tool,
    bridge_registry=app.state.chain_bridge,
    bridge_base_url="http://engine:8001",
)
toolbox.add(tool)
```
