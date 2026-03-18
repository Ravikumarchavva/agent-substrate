# Phase 2 — Multi-Agent Framework Roadmap

> **Status:** Planned — not yet implemented.
> Phase 1 (MemoryScope, ModelContext, Flows, OrchestratorAgent) is complete.
> This document captures the next evolution of the framework. Implement each
> section as a separate PR when the time comes.

---

## 1. SemanticContext — pgvector Long-Term Memory Recall

### What
A fifth `ModelContext` implementation that uses **pgvector** cosine-similarity
search to retrieve the *most semantically relevant* past messages from
long-term Postgres memory, rather than simply the most recent ones.

### Why
`HybridContext` back-fills by recency.  For long-running agents (days / weeks
of history) the most relevant context may be much older than the sliding
window.  Semantic retrieval solves this.

### Design

```python
class SemanticContext(ModelContext):
    def __init__(
        self,
        session_manager: SessionManager,
        embedding_client: BaseEmbeddingClient,  # new ABC
        top_k: int = 10,
        recent_n: int = 5,
        max_total: int = 30,
    ): ...

    async def build(self, *, session_id, current_input, raw_messages, model_client=None):
        # 1. Embed current_input → query vector
        # 2. SELECT ... ORDER BY embedding <=> $query_vec LIMIT top_k  (pgvector)
        # 3. Merge: recent_n most-recent (hot tier) + top_k semantic hits
        # 4. Deduplicate, sort chronologically, prepend SystemMessage
        ...
```

### New ABC

```python
class BaseEmbeddingClient(ABC):
    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]: ...
```

### Schema Migration

```sql
-- Add to MemoryMessage model
ALTER TABLE memory_messages
  ADD COLUMN embedding vector(1536);

CREATE INDEX ON memory_messages
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
```

### Files to create / modify
| File | Action |
|---|---|
| `src/agent_framework/model_clients/base_embedding_client.py` | New ABC |
| `src/agent_framework/model_clients/openai/embedding_client.py` | OpenAI impl |
| `src/agent_framework/context/semantic_context.py` | SemanticContext class |
| `src/agent_framework/context/__init__.py` | Export SemanticContext |
| `src/agent_framework/memory/postgres_memory.py` | Add `embedding` column, vector search method |
| `migrations/` | New Alembic migration for `embedding` column + ivfflat index |

---

## 2. LoopFlow — Iterative Critic–Writer Cycles

### What
A flow type that runs a single agent (or sub-flow) in a loop until a
`stop_condition` predicate returns `True` or `max_iterations` is exhausted.
Designed for critic↔writer patterns where an agent refines its own output.

### Design

```python
class LoopFlow(BaseFlow):
    def __init__(
        self,
        agent: FlowStep,
        stop_condition: Callable[[str], bool],
        max_iterations: int = 5,
        name: str = "loop_flow",
        description: str = "Iterative refinement loop",
        *,
        hooks: Optional[HookManager] = None,
    ): ...

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        accumulated = input_text
        for i in range(self.max_iterations):
            result = await self.agent.run(accumulated, **kwargs)
            output = result.output_text  # convenience property
            if self.stop_condition(output):
                break
            accumulated = f"{accumulated}\n\n[Iteration {i+1} output]:\n{output}"
        return result  # last result

    def to_graph(self) -> FlowGraph:
        # Single node with a self-loop edge labelled "iterate"
        ...
```

### Use Cases
- Writer → Critic → Writer until quality threshold met
- Code generator → Test runner → Code generator until tests pass
- Translation → Back-translation → Compare until BLEU score satisfied

### Files to create / modify
| File | Action |
|---|---|
| `src/agent_framework/agents/flow.py` | Add `LoopFlow` class |
| `src/agent_framework/agents/__init__.py` | Export `LoopFlow` |

---

## 3. Frontend FlowPanel — Live Flow Visualization

### What
A new right-panel component in the chatbot UI that renders the active flow's
`FlowGraph` as an interactive node diagram using **ReactFlow**.  Nodes
highlight in real time as `agent_handoff` SSE events arrive.

### New SSE Event (backend must emit)
```json
{
  "type": "agent_handoff",
  "data": {
    "from_agent": "router",
    "to_agent": "code_agent",
    "reason": "User asked a Python question"
  }
}
```
The `OrchestratorAgent` already fires `HookEvent.HANDOFF` — the
`chat.py` route must forward this as an SSE event via the bridge.

### Component: `FlowPanel.tsx`

```tsx
// src/components/FlowPanel.tsx
"use client"
import ReactFlow, { Node, Edge } from "reactflow"

interface FlowPanelProps {
  graph: FlowGraph | null          // from GET /flows/{name}/graph
  activeAgentId: string | null     // updated on agent_handoff SSE events
}

export function FlowPanel({ graph, activeAgentId }: FlowPanelProps) {
  const nodes: Node[] = graph?.nodes.map(n => ({
    id: n.id,
    data: { label: n.label },
    style: n.id === activeAgentId ? { border: "2px solid #6366f1" } : {},
    position: { x: 0, y: 0 },   // auto-layout via dagre
  })) ?? []
  ...
}
```

### New API Call (`src/lib/api.ts`)
```ts
getFlowGraph: async (flowName: string): Promise<FlowGraph> =>
  fetch(`${API_URL}/flows/${flowName}/graph`).then(r => r.json()),
```

### New SSE handler in `page.tsx`
```ts
case "agent_handoff":
  setActiveAgentId(event.data.to_agent)
  break
```

### Dependencies
```bash
pnpm add reactflow dagre
pnpm add -D @types/dagre
```

### Files to create / modify
| File | Action |
|---|---|
| `src/components/FlowPanel.tsx` | New component |
| `src/types/index.ts` | Add `FlowGraph`, `FlowNode`, `FlowEdge` types |
| `src/lib/api.ts` | Add `getFlowGraph` |
| `src/app/page.tsx` | Handle `agent_handoff` SSE, wire `FlowPanel` |
| `src/app/page.tsx` | Add `activeAgentId` state |

---

## 4. `GET /flows/{name}/graph` — Flow Registry Route

### What
A REST endpoint that returns the serialized `FlowGraph` for a named flow.
Enables the frontend `FlowPanel` and external tooling (docs, debugging).

### Design

```python
# src/agent_framework/server/routes/flows.py
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/flows", tags=["flows"])

@router.get("/{name}/graph")
async def get_flow_graph(name: str, request: Request):
    flows: dict = getattr(request.app.state, "flows", {})
    flow = flows.get(name)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Flow '{name}' not found")
    graph = flow.to_graph()
    return graph.to_dict()
```

### Registration in `app.py`
```python
# In lifespan, after agent setup:
app.state.flows = {
    "main": orchestrator_agent,   # OrchestratorAgent has to_graph()
    "pipeline": my_sequential_flow,
}
```

### Files to create / modify
| File | Action |
|---|---|
| `src/agent_framework/server/routes/flows.py` | New route |
| `src/agent_framework/server/app.py` | Mount router, populate `app.state.flows` |

---

## 5. Evaluation Harness Extension for Flows

### What
Extend the existing evaluation infrastructure so `EvalRunner` can accept a
`BaseFlow` (or `OrchestratorAgent`) as the subject under test, not just a
`ReActAgent`.

### Design

```python
class EvalRunner:
    def __init__(
        self,
        subject: Union[BaseAgent, BaseFlow],  # ← extend to accept BaseFlow
        dataset: EvalDataset,
        metrics: List[BaseMetric],
    ): ...

    async def run(self) -> EvalReport:
        for case in self.dataset:
            if isinstance(self.subject, BaseAgent):
                result = await self.subject.run(case.input)
            else:
                result = await self.subject.run(case.input)  # same interface!
            ...
```

Because `BaseFlow.run()` returns `AgentRunResult` (same as `BaseAgent.run()`),
no special-casing is needed beyond accepting the union type.

### Flow-Specific Metrics
- **HandoffAccuracy**: Were handoffs made to the correct sub-agent?
- **FlowCompletionRate**: What fraction of flows reached the output node?
- **ParallelBranchDivergence**: How much do parallel branch outputs differ?

### Files to create / modify
| File | Action |
|---|---|
| `src/agent_framework/eval/runner.py` | Extend `EvalRunner` type hints |
| `src/agent_framework/eval/metrics/flow_metrics.py` | New flow-specific metrics |
| `src/agent_framework/eval/__init__.py` | Export new metrics |

---

## 6. Implementation Order Recommendation

When picking up Phase 2, tackle in this order:

1. **`LoopFlow`** — pure Python, no infra changes, high value for iterative agents.
2. **`GET /flows/{name}/graph` route** — backend-only, unlocks frontend work.
3. **`FlowPanel.tsx`** — once the route exists, the UI can be built independently.
4. **`SemanticContext`** — requires pgvector migration; coordinate with DB ops.
5. **Eval harness** — low priority but important for regression safety before prod.

---

*Last updated: Phase 1 completion. Authored by GitHub Copilot.*
