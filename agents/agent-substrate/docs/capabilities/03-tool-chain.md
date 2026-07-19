# 3 · Tool Chain

Tool Chain lets the LLM write a **single Python script** that calls multiple tools and pipes results between them — all inside the existing CodeInterpreter sandbox. Instead of the ReAct loop dispatching one tool at a time, the LLM writes the orchestration logic itself.

## Why this matters

In a normal ReAct loop: `query_db` → (wait) → (LLM processes) → `send_email` → (wait) — each hop re-enters the LLM.  
With Tool Chain: the LLM writes `data = tools.query_db(...); tools.send_email(body=data.text)` — the two calls happen inside one sandbox execution with no LLM round-trips between them.

Large data never re-enters the sandbox as text either — it is passed **by reference** (a `DataRef` pointer) store-to-tool.

## Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TB
    classDef llm   fill:#E3F2FD,stroke:#1565C0,color:#0D47A1,font-weight:bold
    classDef l2    fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef l1    fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef infra fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2
    classDef data  fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C

    LLM["LLM — writes Python script<br/>using tools.name(...) calls"]:::llm

    subgraph CHAIN["ToolChainTool (L2 — capabilities/tools/chain/)"]
        direction TB
        TCT["ToolChainTool · tool.py<br/>name = 'tool_chain'<br/>execute(code, timeout?) → ToolExecutionResult<br/>requires CodeInterpreterTool + ToolInvoker"]:::l2
        PRE["build_prelude() · prelude.py<br/>stdlib-only Python preamble<br/>injects tools namespace, ToolResult, artifacts.put()"]:::l2
        BRIDGE["BridgeSession · bridge.py<br/>per-chain token (32-byte, single-use)<br/>invoke(ToolCallRequest) → InvocationResult<br/>deregister() invalidates token"]:::l2
        REG["ChainBridgeRegistry · bridge.py<br/>sessions: dict[chain_id, BridgeSession]"]:::l2
        TCT ~~~ PRE ~~~ BRIDGE ~~~ REG
    end

    VM["CodeInterpreter — K8s agent-sandbox / local sandbox container<br/>POST /ci/run {code, session_id}<br/>returns stdout, exit_code, media_blocks"]:::infra
    INV["ToolInvoker (L1) · agents/tools/invoker.py<br/>invoke(call, session, ctx)<br/>risk check → approval gate (HIGH/CRITICAL)"]:::l1
    DRS["DataRefStore · pipeline/data_ref.py<br/>< 1MB → Redis · ≥ 1MB → S3/MinIO<br/>returns DataRef.ref_id"]:::data

    LLM -->|"tool_chain(code=script)"| CHAIN
    TCT -->|"execute(prelude + user code)"| VM
    VM -->|"POST /internal/chain/{id}/invoke (Bearer)"| BRIDGE
    BRIDGE -->|"invoke(ToolCallRequest)"| INV
    INV -->|"offload large results"| DRS
    INV -->|"ToolExecutionResult"| BRIDGE
    BRIDGE -->|"InvocationResult (text, structured, ref)"| VM
    VM -->|"exec result"| TCT
```

## End-to-end execution sequence

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant LLM
    participant TCT as ToolChainTool
    participant BRIDGE as BridgeSession
    participant INV as ToolInvoker (L1)
    participant DRS as DataRefStore
    participant VM as Sandbox VM

    LLM->>TCT: tool_chain(code="data=query_db(...) → send_email(...)")

    TCT->>TCT: build_prelude(tool_names, bridge_url, chain_id, token)
    TCT->>BRIDGE: BridgeSession(chain_id, invoker, token) + registry.register
    TCT->>VM: execute(prelude + user code, timeout=policy.total_timeout_s)
    VM->>VM: run prelude — set up tools namespace + bridge token

    rect rgb(232, 234, 246)
        Note over VM,INV: First tool call — query_db
        VM->>BRIDGE: POST /chain/{id}/invoke (Bearer) ToolCallRequest{name, args, call_id}
        BRIDGE->>INV: invoke(call, session, ctx)
        Note over BRIDGE,INV: risk check → SAFE proceeds<br/>HIGH/CRITICAL: approval gate (HITL)
        INV->>INV: tool.execute(**kwargs)
        INV->>DRS: result > 1MB → store(bytes) → DataRef
        INV-->>BRIDGE: ToolExecutionResult(content, structured, artifact_ref)
        BRIDGE-->>VM: InvocationResult{text, structured, ref:"ref_abc123"}
        VM->>VM: data = ToolResult(text, ref="ref_abc123")
    end

    rect rgb(232, 244, 232)
        Note over VM,INV: Second call — pass-by-reference
        VM->>BRIDGE: POST /chain/{id}/invoke → send_email(body={"$artifact":ref})
        BRIDGE->>DRS: resolve(ref) → bytes
        BRIDGE->>INV: invoke(call with resolved payload)
        INV-->>BRIDGE: ToolExecutionResult
        BRIDGE-->>VM: InvocationResult
    end

    VM-->>TCT: exec_result (stdout + media_blocks)
    TCT->>BRIDGE: deregister(chain_id) — token invalidated in finally
    TCT-->>LLM: ToolExecutionResult(summary + ChainRunResult)
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

`ToolChainTool` requires an active code interpreter (`K8sSandboxCodeInterpreterTool` or `LocalSandboxCodeInterpreterTool`, gated by `CI_LOCAL_SANDBOX_URL`) — if neither is available, the constructor raises `RuntimeError` and the tool is simply not registered.

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
