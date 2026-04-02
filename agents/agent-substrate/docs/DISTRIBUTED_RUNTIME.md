# Distributed Runtime

> **Status:** Architecture finalised. See [`DISTRIBUTED_ARCHITECTURE.md`](DISTRIBUTED_ARCHITECTURE.md) for full design
> and [`DISTRIBUTED_IMPLEMENTATION_PLAN.md`](DISTRIBUTED_IMPLEMENTATION_PLAN.md) for phased implementation.

## Architecture Summary

**Restate** (durable execution engine) + **NATS JetStream** (real-time SSE streaming).
Agents run as HTTP service workers, each ReAct step is checkpointed via `ctx.run()`,
HITL uses `ctx.promise()` for zero-resource suspension, and crash recovery replays
from the Restate journal without re-executing completed steps.

## Previous State

Raavan was a **single-process** framework. Both the monolith (`server/`) and the microservices (`services/`) ran agents in-process within the same Python event loop. This meant:

- All agent state lived in memory or Redis — no cross-process agent communication.
- Sub-agents handed off via `OrchestratorAgent` ran in the same event loop on the same machine.
- If a worker process restarted mid-run, the run was lost (Redis preserved the conversation messages but not the agent's mid-loop execution state).
- You could not run individual agents on separate machines or scale them independently.

---

## What "Distributed Runtime" Means

A distributed runtime lets you treat agents as isolated units of work that can:

1. Be dispatched to worker processes or machines via a message queue.
2. Resume execution after a crash by replaying from a durable checkpoint.
3. Be scaled independently — e.g. 5 replicas of `code_interpreter_agent`, 1 replica of `orchestrator_agent`.
4. Communicate back results to the originating coordinator via the same queue.

---

## Implementation Path

### Option A — Temporal.io (recommended)

Raavan already has a `catalog/_temporal/` stub. Temporal is a durable workflow engine that provides exactly-once execution, automatic retries, and state replay.

**What to build:**

1. **`TemporalAgentActivity`** — wrap `ReActAgent.run()` as a Temporal activity. Activities are the unit of work that Temporal can retry, timeout, and execute on any worker.

   ```python
   # catalog/_temporal/activities.py
   @activity.defn
   async def run_agent_activity(payload: AgentActivityPayload) -> AgentActivityResult:
       agent = build_agent_from_payload(payload)
       result = await agent.run(payload.input_text)
       return AgentActivityResult(output=result.output, usage=result.usage)
   ```

2. **`AgentWorkflow`** — a Temporal workflow that orchestrates one or more agent activities. The workflow itself is pure Python but runs durably — Temporal replays it on resume, so the workflow function must be deterministic (no `datetime.now()`, no random — use Temporal's side-effect APIs).

   ```python
   # catalog/_temporal/workflows.py
   @workflow.defn
   class AgentWorkflow:
       @workflow.run
       async def run(self, job: JobPayload) -> WorkflowResult:
           result = await workflow.execute_activity(
               run_agent_activity,
               AgentActivityPayload(input_text=job.input),
               start_to_close_timeout=timedelta(minutes=10),
           )
           return WorkflowResult(output=result.output)
   ```

3. **`TemporalWorker`** — a worker process that registers workflows and activities and polls Temporal's task queue. Deploy as many replicas as needed.

   ```python
   # catalog/_temporal/worker.py
   async def start_worker():
       async with Client.connect("localhost:7233") as client:
           worker = Worker(
               client,
               task_queue="agent-tasks",
               workflows=[AgentWorkflow],
               activities=[run_agent_activity],
           )
           await worker.run()
   ```

4. **Wire into `job_controller` service** — instead of dispatching jobs via an in-process call, submit a Temporal workflow start. The `job_controller` polls Temporal for completion and updates `JobRun` status.

5. **Checkpointing** — because Temporal replays the workflow function on resume, each activity is already a natural checkpoint. Add explicit `workflow.set_query_handler` to expose current step to the frontend.

**Infrastructure:** Add `temporalio` to `pyproject.toml`. Deploy `temporalio/server` alongside the existing Docker Compose stack. One Temporal namespace per environment.

---

### Option B — Redis Streams + Worker Pool (lighter weight)

If Temporal is too heavy, a simpler approach using the existing Redis infrastructure:

1. `job_controller` pushes job payloads onto a Redis Stream (`XADD agent-jobs`).
2. N worker processes each run a consumer group loop (`XREADGROUP`). Each worker pulls a job, instantiates the agent, runs it, and writes the result back.
3. `job_controller` polls for the result key and updates `JobRun` status.
4. Workers can run on separate machines — horizontal scaling is just adding more worker replicas.

**Checkpointing:** After each `StepResult` in the ReAct loop, serialize the step to Redis with a `HSET agent-checkpoint:{run_id}` key. On worker failure, a new worker picks up the job and can optionally fast-forward by reloading completed steps.

**Limitations vs Temporal:** No automatic retry with backoff at the workflow level, no time-travel replay, no built-in visibility UI (Temporal has a web UI for this).

---

## Multi-Agent Distribution (Handoff Across Machines)

`OrchestratorAgent` today runs sub-agents in-process via `_HandoffTool`. To distribute sub-agents:

1. Each sub-agent becomes a named **agent service** with an HTTP endpoint (`POST /run`).
2. Replace `_HandoffTool.execute()` with an async HTTP call to the target agent service.
3. The gateway routes to the correct service by agent name. Auth uses the existing JWT middleware.

This is close to Option A's activity model — the difference is that the sub-agent's HTTP handler is the unit of work instead of a Temporal activity.

---

## What Needs to Change in the Codebase

| Component | Change needed |
|---|---|
| `catalog/_temporal/` | Implement `activities.py`, `workflows.py`, `worker.py` |
| `services/job_controller/` | Submit Temporal workflow instead of direct in-process dispatch |
| `services/agent_runtime/` | Becomes a Temporal worker (or Redis Streams consumer) |
| `server/sse/bridge.py` | `WebHITLBridge` must be backed by Redis pub/sub (not in-memory) so any worker can push events to the SSE connection on any gateway pod |
| `shared/tasks/store.py` | `TaskStore` must be Redis-backed (currently in-memory — would lose Kanban state on worker restart) |
| Kubernetes | Add Temporal Server deployment to `k8s/base/`; add worker Deployment separate from the gateway |

---

## What Does Not Need to Change

- `BaseAgent`, `ReActAgent`, `OrchestratorAgent` — the agent loop itself is stateless between calls and does not need modification.
- `BaseMemory` / `RedisMemory` — conversation history is already durable; workers can restore it via `await memory.restore()`.
- `BaseTool` / guardrails — all tool logic is pure async; no changes needed.
- The frontend / SSE protocol — the client just receives the same SSE event types; it doesn't care whether the agent runs in-process or on a remote worker.

---

## Recommended Sequence

1. Back `TaskStore` with Redis (in-memory → Redis) — isolated, high value, unblocks distributed workers from sharing Kanban state.
2. Back `WebHITLBridge` with Redis pub/sub — required before workers can run on separate processes.
3. Implement Redis Streams worker pool (Option B) — delivers horizontal scale without adding new infrastructure.
4. Evaluate Temporal (Option A) if you need durable workflows, time-travel debugging, or visibility UI.
