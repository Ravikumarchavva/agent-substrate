# Distributed Runtime — Implementation Plan

## Phase Overview

| Phase | Focus | Deliverable |
|---|---|---|
| **Phase 1** | Foundation | NATS + Restate infra, `distributed/` package skeleton |
| **Phase 2** | Core Workflow | Durable ReAct loop in Restate, LLM streaming via NATS |
| **Phase 3** | Tool Execution | Per-tool policies, idempotency, DataRef integration |
| **Phase 4** | HITL | Durable promises for ask_human + tool_approval |
| **Phase 5** | Gateway Rewrite | Replace monolith chat route with Restate + NATS |
| **Phase 6** | Multi-Agent | OrchestratorAgent via Restate service calls |
| **Phase 7** | Production Hardening | Cancel, observability, error handling, TaskStore |
| **Phase 8** | Deployment | Docker Compose + k8s manifests, worker scaling |

---

## Phase 1: Foundation

### 1.1 Add dependencies
```toml
# pyproject.toml
restate-sdk = ">=0.7"
nats-py = ">=2.9"
```

### 1.2 Docker Compose infra
- Add `restate` service (port 8080/9070)
- Add `nats` service with JetStream (port 4222/8222)
- Add volumes for persistence

### 1.3 Package skeleton
```
src/raavan/distributed/
├── __init__.py
├── workflow.py          (stub)
├── activities.py        (stub)
├── policies.py          (stub)
├── streaming.py         (NATSStreamingBridge)
├── restate_app.py       (Restate app setup)
├── worker.py            (worker entry point)
└── client.py            (Restate client wrapper)
```

### 1.4 NATS streaming bridge
- `NATSStreamingBridge.connect()` / `disconnect()` / `publish()` / `subscribe()`
- JetStream stream `AGENT_EVENTS` with subject pattern `agent.events.{thread_id}`
- 1-hour retention per subject

### 1.5 Settings
```python
# configs/settings.py additions
RESTATE_INGRESS_URL: str = "http://localhost:8080"
RESTATE_ADMIN_URL: str = "http://localhost:9070"
NATS_URL: str = "nats://localhost:4222"
```

### 1.6 Verification
- Restate server starts and accepts registrations
- NATS JetStream accepts publish/subscribe
- Unit tests for NATSStreamingBridge

---

## Phase 2: Core Workflow

### 2.1 Restate AgentWorkflow
- `workflow.py`: `AgentWorkflow` with `run()` main handler
- Durable ReAct loop: `ctx.run("llm_call")` → `ctx.run("tool_exec")` → repeat
- Max iterations from payload

### 2.2 LLM Activity
- `activities.py`: `do_llm_call()` — call LLM, publish text_delta to NATS
- Restore memory from Redis at start
- Handle streaming callback → NATS publish (ephemeral tokens)
- Return full response for journal persistence

### 2.3 Restate App Registration
- `restate_app.py`: Create Restate app, bind `AgentWorkflow`
- `worker.py`: Entry point that serves the Restate handler via hypercorn/uvicorn
- Register with Restate admin API on startup

### 2.4 Verification
- Start worker, submit workflow via Restate HTTP API
- Verify LLM call executes and tokens appear in NATS
- Kill worker mid-LLM-call, verify retry on restart

---

## Phase 3: Tool Execution

### 3.1 Tool Policies
- `policies.py`: `ToolPolicy` dataclass + `TOOL_POLICIES` dict
- Derive from existing `BaseTool.risk` and `BaseTool.hitl_mode` attributes
- timeout, needs_idempotency, requires_approval, is_hitl_input, large_payload

### 3.2 Tool Activity
- `activities.py`: `do_tool_exec()` — lookup tool, apply policy, execute
- Idempotency key via `ctx.uuid()` for critical tools
- DataRef pattern for large results (>32KB → S3/file store)
- Timeout enforcement via `asyncio.wait_for()`

### 3.3 Tool Result Publishing
- Publish `tool_call` event to NATS before execution
- Publish `tool_result` event to NATS after execution
- Include risk/color metadata from tool schema

### 3.4 Verification
- Execute workflow with tool-calling agent
- Verify tool results appear in NATS
- Test DataRef for large tool output (web_surfer HTML)
- Test idempotency: kill worker after tool exec, verify no re-execution

---

## Phase 4: HITL

### 4.1 Durable Promises for Tool Approval
- In workflow: `ctx.promise(f"approval-{request_id}")` suspends execution
- Publish `tool_approval_request` to NATS
- Frontend shows ToolApprovalCard
- User response → API call → resolves Restate promise

### 4.2 Durable Promises for Human Input
- In workflow: `ctx.promise(f"human-input-{request_id}")` suspends execution
- Publish `human_input_request` to NATS
- Frontend shows HumanInputCard
- User response → API call → resolves Restate promise

### 4.3 HITL Resolution Endpoint
- New route: `POST /chat/respond/{request_id}` → resolves Restate promise
- Payload: `{workflow_id, promise_type, payload}`
- Works identically to existing endpoint but backed by Restate

### 4.4 Verification
- Start workflow that triggers ask_human
- Verify workflow suspends (0 resource consumption)
- Resolve promise via API
- Verify workflow resumes with human input
- Kill worker while HITL is pending — verify promise survives restart

---

## Phase 5: Gateway Rewrite

### 5.1 New Chat Route
- Rewrite `server/routes/chat.py`:
  - Start Restate workflow (non-blocking)
  - Subscribe to NATS subject for thread_id
  - Stream events as SSE to frontend
- Remove in-process agent execution
- Remove WebHITLBridge (replaced by Restate promises)

### 5.2 NATS SSE Fan-out
- Gateway subscribes to `agent.events.{thread_id}`
- Each SSE event = one NATS message
- Handle client disconnect: unsubscribe from NATS (agent continues)
- Handle client reconnect: NATS JetStream replay from last sequence

### 5.3 Persistence
- Worker persists messages to Postgres (inside ctx.run)
- Gateway does NOT persist (read-only proxy)
- Existing `persist_assistant_message`, `persist_tool_result` move to activities

### 5.4 Verification
- Full end-to-end: frontend → gateway → Restate → worker → NATS → SSE → frontend
- Compare SSE event format with existing (must be identical)
- Test disconnect/reconnect
- Test cancel via `POST /chat/{thread_id}/cancel`

---

## Phase 6: Multi-Agent

### 6.1 Sub-Agent Dispatch
- OrchestratorAgent hand-off → `ctx.service_call("AgentWorkflow", payload)`
- Each sub-agent is a separate Restate workflow invocation
- Results flow back to orchestrator durably

### 6.2 Cross-Agent Communication
- Sub-agents share thread_id (same conversation memory)
- Each sub-agent publishes to same NATS subject
- Frontend sees unified SSE stream

### 6.3 Verification
- OrchestratorAgent dispatches to 2 sub-agents
- Kill sub-agent worker → verify restart + result delivery
- Verify orchestrator receives sub-agent results after crash recovery

---

## Phase 7: Production Hardening

### 7.1 Cancel/Abort
- `POST /chat/{thread_id}/cancel` → Restate cancel API
- Agent stops at next ctx.run checkpoint
- Publish `cancelled` event to NATS

### 7.2 Distributed Tracing
- Propagate OTEL trace context through Restate invocations
- Restate invocation ID as span attribute
- NATS message headers carry trace context

### 7.3 Error Handling
- Retry policies per activity (LLM: 3 retries, tool: 2 retries)
- Dead letter handling for permanently failed workflows
- Error events published to NATS for frontend display

### 7.4 Redis-Backed TaskStore
- Migrate `shared/tasks/store.py` from in-memory to Redis
- TaskStore reads/writes via Redis hashes
- Kanban state survives worker restart

### 7.5 Verification
- Chaos testing: random worker kills during various workflow stages
- Verify no lost messages, no duplicate executions
- Load test: 100 concurrent workflows

---

## Phase 8: Deployment

### 8.1 Docker Compose
- Update `docker-compose.yml` with Restate + NATS services
- Worker service with `uv run python -m raavan.distributed.worker`
- Health checks for all services

### 8.2 Kubernetes
- Restate Deployment (1 replica dev, 3 HA prod)
- NATS StatefulSet (3 replicas with JetStream clustering)
- Agent Worker Deployment with HPA (auto-scale on CPU/memory)
- Ingress rules for Restate admin (internal only)

### 8.3 Settings & Env Vars
- `RESTATE_INGRESS_URL` (internal, not NEXT_PUBLIC)
- `NATS_URL` (internal)
- Worker-specific: `WORKER_CONCURRENCY`, `WORKER_TASK_QUEUE`

### 8.4 Verification
- `deploy.py` updated for new services
- Smoke test: end-to-end chat in Kind cluster
- Rolling update: deploy new worker version, verify zero downtime
