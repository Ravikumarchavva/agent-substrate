# Production Readiness — Agent Framework Gap Analysis

> **Status as of March 2026** — current system is a well-structured local development scaffold.  
> This document identifies every gap between the current state and a genuinely production-grade, horizontally scalable agentic system.

---

## TL;DR Scorecard

| Area | Current | Required for Prod | Severity |
|---|---|---|---|
| HITL / SSE state | In-process `asyncio.Queue` | Redis pub/sub or message broker | 🔴 Critical |
| TaskStore | In-memory `dict` | Redis or PostgreSQL | 🔴 Critical |
| Backend auth | None (CORS `*`) | JWT / API key middleware | 🔴 Critical |
| Replica safety | Single process only | Stateless pods + shared broker | 🔴 Critical |
| OAuth tokens | In-memory dict | Redis / encrypted DB column | 🔴 Critical |
| Redis usage | Defined, not wired | Wire `RedisMemory` + HITL bus | 🔴 Critical |
| Agent memory | Unbounded in-process | Redis-backed sliding window | 🟠 High |
| Model routing | Hardcoded `gpt-4o-mini` | Router with fallback | 🟠 High |
| Rate limiting | None | Per-user token bucket | 🟠 High |
| Secret management | `.env` file | Vault / K8s Secrets | 🟠 High |
| Log aggregation | stdout only | Loki + structured JSON | 🟠 High |
| CI/CD | None | GitHub Actions + Docker push | 🟠 High |
| Kubernetes manifests | None | Deployments, Services, HPA | 🟠 High |
| Graceful shutdown | None | Drain SSE, cancel agents | 🟡 Medium |
| Background tasks | Blocked on SSE conn | Celery / ARQ task queue | 🟡 Medium |
| Idempotency | None | Request dedup by `request_id` | 🟡 Medium |
| Observability | Traces only | Traces + metrics + logs unified | 🟡 Medium |
| Cost tracking | None | Token usage per user/thread | 🟡 Medium |

---

## 1. 🔴 Critical — Breaks Immediately with Multiple Replicas

### 1.1 WebHITLBridge is in-memory only

**File:** `src/agent_framework/web_hitl.py`

```python
# Current: asyncio.Queue per process, asyncio.Future per request_id
class WebHITLBridge:
    _outgoing: asyncio.Queue   # lives in ONE pod
    _pending: dict[str, asyncio.Future]  # lives in ONE pod
```

**Problem:** When you run 2+ backend replicas, the SSE stream for a user lands on pod A, but the `/chat/respond/{id}` POST resolves the Future on pod B — the Future never resolves. Every HITL interaction silently times out.

**Fix:** Replace with Redis pub/sub:
- Outgoing events: `PUBLISH hitl:events:{session_id} <json>`  
- Pending approval: `SET hitl:pending:{request_id} <json> EX 300` + subscribe for resolution  
- SSE generator subscribes to Redis channel per session

---

### 1.2 TaskStore is in-memory

**File:** `src/agent_framework/tasks/store.py`

```python
class TaskStore:
    _lists: Dict[str, TaskList] = {}   # gone on restart
    _by_conversation: Dict[str, str]   # gone on restart
```

**Problem:** All Kanban tasks are lost on any pod restart or rolling deploy. Multi-replica = tasks randomly missing across pods.

**Fix:**
- Persist `TaskList` and `Task` as SQLAlchemy models in PostgreSQL  
- Or use Redis Hash with `hset tasks:{thread_id} ...` (TTL-based, acceptable for ephemeral boards)  
- Either way, emit SSE updates via Redis pub/sub, not in-process queue

---

### 1.3 No authentication on the FastAPI backend

**File:** `src/agent_framework/server/app.py` line 241

```python
allow_origins=["*"],   # any origin
```

The backend has zero authentication middleware. Any client that knows the URL can POST `/chat`, list threads, access Spotify OAuth tokens, or trigger code execution.

**Fix:**
- Add `Authorization: Bearer <token>` validation middleware  
- Issue short-lived JWTs from Next.js `/api/auth` routes, validate on every FastAPI request  
- Internal service-to-service calls use a shared secret header  
- CORS `allow_origins` must be locked to your actual domain(s)

---

### 1.4 OAuth tokens stored in a Python dict

**File:** `src/agent_framework/server/routes/spotify_oauth.py` line 18

```python
# In-memory token storage (use Redis/database in production)
_token_store: Dict[str, dict] = {}
```

**Problem:** Tokens lost on restart. Not accessible across replicas. Any code execution vulnerability leaks all tokens.

**Fix:** Store encrypted in PostgreSQL (column-level encryption via `pgcrypto`) or Redis with TTL matching token expiry.

---

### 1.5 Redis is defined but never wired

`RedisMemory` class is fully implemented in `src/agent_framework/memory/redis_memory.py` and Redis runs in `docker-compose.yml`. But `app.py` creates agents with `UnboundedMemory` and the HITL bridge never touches Redis.

**Fix (two-line change to start):**
```python
# In lifespan, replace UnboundedMemory with:
from agent_framework.memory.redis_memory import RedisMemory
memory = RedisMemory(redis_url=settings.REDIS_URL, session_id=thread_id)
```
Then extend `WebHITLBridge` to use Redis pub/sub for cross-replica routing.

---

## 2. 🟠 High — Will Cause Incidents in Production

### 2.1 Unbounded agent memory

**File:** `src/agent_framework/agents/react_agent.py`

Each agent run creates a fresh `UnboundedMemory`. As a conversation grows, the entire history is passed to OpenAI on every turn — no truncation, no summarization. A 200-message thread will hit context limits and cost 10x a normal conversation.

**Fix:**
- Use `RedisMemory` with a sliding window (`SESSION_MAX_MESSAGES = 200` is already in settings, just not enforced)  
- Add a summarization step when context exceeds 80% of model limit  
- Checkpoint to `PostgreSQL` every N messages (`SESSION_AUTO_CHECKPOINT` setting exists, not wired)

---

### 2.2 Hardcoded model with no fallback

**File:** `src/agent_framework/server/app.py` line 77

```python
app.state.model_client = OpenAIClient(model="gpt-4o-mini", ...)
```

**Problem:** Any OpenAI outage = total service outage. No retry, no fallback, no model router.

**Fix:**
- Implement a `ModelRouter` that tries `gpt-4o-mini` → `gpt-4o` → Anthropic Claude as fallback chain  
- Add exponential backoff + circuit breaker (the `resilience.py` module in the repo is a good start — wire it to the OpenAI client)  
- Expose model selection per-thread in the API

---

### 2.3 No rate limiting

Any user can send unlimited messages, triggering unlimited OpenAI API calls and tool executions.

**Fix:**
- Redis token bucket: `INCR rate:{user_id}:{minute}` with `EXPIRE`  
- FastAPI middleware that reads `Authorization` header, checks Redis, returns `429` with `Retry-After`  
- Per-user daily token budget enforced at the agent level

---

### 2.4 Secrets in `.env` files

Both `.env` (backend) and `.env.local` (frontend) contain `OPENAI_API_KEY`, `SPOTIFY_CLIENT_SECRET`, `DATABASE_URL`. These files exist on developer machines and may be accidentally committed.

**Fix:**
- **Dev:** Use `direnv` + `.env` (already gitignored ✅)  
- **CI:** GitHub Actions Secrets → injected at build time  
- **Prod K8s:** `kubectl create secret generic agent-secrets --from-env-file=.env` then mount as env vars in pod spec  
- **Long term:** HashiCorp Vault or AWS Secrets Manager with dynamic credentials

---

### 2.5 No structured log aggregation

Logs go to stdout via Python's `logging` module. On Kubernetes, these are visible in `kubectl logs` but not searchable, not correlated with traces, and gone when the pod restarts.

**Fix:**
- Add `python-json-logger` to emit structured JSON  
- Add `trace_id` and `span_id` from OpenTelemetry context to every log line  
- Deploy **Grafana Loki** (add to `docker-compose.yml`) with Promtail as log shipper  
- Grafana already running — add Loki as datasource, create unified dashboard (logs + traces + metrics)

---

### 2.6 No Kubernetes manifests

The attached diagram shows the target K8s architecture but no manifests exist.

**Minimum viable K8s setup needed:**

```
k8s/
  namespace.yaml
  postgres/
    statefulset.yaml        # or use managed RDS
    service.yaml
    pvc.yaml
  redis/
    deployment.yaml
    service.yaml
  backend/
    deployment.yaml         # agent-framework, replicas: 2+
    service.yaml            # ClusterIP
    hpa.yaml                # HorizontalPodAutoscaler (CPU + custom metrics)
    configmap.yaml
    secret.yaml             # refs to K8s secrets
  frontend/
    deployment.yaml         # next-js-app, replicas: 2+
    service.yaml            # ClusterIP
  ingress/
    ingress.yaml            # NGINX ingress with TLS termination
    cert.yaml               # cert-manager ClusterIssuer
```

---

### 2.7 No CI/CD pipeline

No `Dockerfile` for either project, no build pipeline, no container registry.

**Minimum pipeline (GitHub Actions):**

```yaml
# .github/workflows/deploy.yml
on: [push to main]
jobs:
  build-backend:
    - uv run pytest            # tests
    - docker build -t agent-framework:$SHA .
    - docker push gcr.io/.../agent-framework:$SHA
    - kubectl set image deployment/agent-framework ...
  build-frontend:
    - pnpm build               # TypeScript check
    - docker build -t chatbot-ui:$SHA .
    - docker push ...
    - kubectl set image deployment/next-js-app ...
```

---

## 3. 🟡 Medium — Will Cause Poor UX and Operational Pain

### 3.1 No graceful shutdown

When a Kubernetes pod is terminated (rolling deploy, scale-down), active SSE connections are cut immediately. Users see the chat freeze mid-response with no error message.

**Fix:**
- Handle `SIGTERM`: stop accepting new `/chat` requests, drain in-flight agent runs with a 30-second grace period  
- Frontend: detect SSE disconnect and show "Reconnecting..." with exponential backoff  
- Persist partial agent output to DB so reconnect can resume

---

### 3.2 Long agent runs block the SSE connection

A 5-minute agent run holds one FastAPI async worker and one SSE HTTP connection open for the entire duration. If the load balancer has a 60-second timeout, the connection is killed.

**Fix:**
- Move agent execution to a **background task queue** (ARQ with Redis, or Celery)  
- POST `/chat` returns a `run_id` immediately  
- Frontend polls or subscribes to `GET /chat/stream/{run_id}` (SSE from Redis pub/sub)  
- Allows the HTTP layer to be stateless and the agent to outlive any single connection

---

### 3.3 No request idempotency

If a user double-clicks send or the network retries a POST, duplicate agent runs are spawned. No `request_id` deduplication exists.

**Fix:**
- Frontend generates a `request_id = nanoid()` per send, attaches as header  
- Backend checks `Redis SETNX active:{request_id}` — reject if already processing  
- Return cached response for identical `(thread_id, request_id)` pairs

---

### 3.4 No per-user cost / token tracking

OpenAI usage (tokens in/out) is measured in `AgentRunResult.usage` but never persisted or exposed. In production with real users this becomes an uncontrolled cost center.

**Fix:**
- Persist `UsageStats` to a `token_ledger` table per thread/user/model  
- Dashboard in Grafana showing daily cost per user, model, and tool  
- Hard limit: if `user.monthly_tokens > limit` → pause service, send email

---

### 3.5 WebSurferTool added unsafely per request

**File:** `src/agent_framework/server/routes/chat.py` line 50

```python
if not any(isinstance(t, WebSurferTool) for t in tools):
    tools.append(WebSurferTool())
```

`WebSurferTool` is instantiated fresh every request with no cleanup, no connection pooling, and no sandboxing. Web requests triggered by the agent go out with the backend's IP/identity.

**Fix:**
- Pre-instantiate `WebSurferTool` in `lifespan` alongside other tools  
- Add `robots.txt` compliance and allow-list for permitted domains  
- In K8s: run web browsing in a separate sandboxed pod with `NetworkPolicy` egress rules

---

### 3.6 No distributed trace correlation from frontend to backend

OpenTelemetry traces start at the FastAPI layer. There's no `traceparent` header injected by the Next.js proxy, so frontend → backend causality is invisible in Grafana Tempo.

**Fix:**
```typescript
// In /api/chat/route.ts — propagate W3C trace context
const res = await fetch(`${BACKEND_URL}/chat`, {
  headers: {
    ...req.headers,
    traceparent: generateTraceParent(),
  },
});
```
```python
# FastAPI: extract incoming traceparent automatically via OTel propagator
# (already works if propagators are configured — just need the header forwarded)
```

---

## 4. Target Architecture for Scalable Agentic System

```
                    ┌─────────────────────────────────────────────┐
                    │            Kubernetes Cluster                │
                    │                                              │
Users ──HTTPS──► [Ingress NGINX + TLS]                           │
                    │         │                                    │
              ┌─────▼─────┐   │                                   │
              │ Next.js   │   │                                   │
              │ (2 pods)  │   │ Internal ClusterIP                │
              │ Stateless │   │                                   │
              └─────┬─────┘   │                                   │
                    │ POST /chat (with JWT + traceparent)          │
              ┌─────▼──────────────────┐                          │
              │  FastAPI Backend       │                          │
              │  (2+ pods, stateless)  │                          │
              │  - Auth middleware     │                          │
              │  - Rate limiting       │                          │
              │  - Route to ARQ queue  │                          │
              └─────┬──────────────────┘                          │
                    │ enqueue(run_agent, thread_id, message)       │
              ┌─────▼──────────────────┐                          │
              │   ARQ Worker Pool      │                          │
              │  (agent pods, 2-10)    │                          │
              │  - ReActAgent runs     │                          │
              │  - RedisMemory         │                          │
              │  - Tool execution      │                          │
              └─────┬──────────────────┘                          │
                    │ PUBLISH events to Redis channel              │
              ┌─────▼──────┐  ┌──────────┐  ┌──────────────────┐ │
              │ PostgreSQL │  │  Redis   │  │ Grafana Stack    │ │
              │ (primary + │  │ pub/sub  │  │ Tempo + Loki +   │ │
              │  replica)  │  │ memory   │  │ Prometheus       │ │
              │ threads    │  │ HITL bus │  │                  │ │
              │ messages   │  │ sessions │  └──────────────────┘ │
              │ token ledgr│  │ rate lmt │                        │
              └────────────┘  └──────────┘                        │
                    │                                              │
              ┌─────▼──────────────────┐                          │
              │  External Services     │                          │
              │  OpenAI (+ fallback)   │                          │
              │  Spotify API           │                          │
              │  Google OAuth          │                          │
              └────────────────────────┘                          │
                                                                   │
              ┌─────────────────────────────────────┐             │
              │  Code Interpreter Sandbox            │             │
              │  (isolated pods, NetworkPolicy)      │             │
              │  Firecracker microVMs or gVisor      │             │
              └─────────────────────────────────────┘             │
                                                                   │
                    └─────────────────────────────────────────────┘
```

### Key architectural principles for agentic systems

1. **Stateless API pods** — All state lives in Redis or PostgreSQL. Any pod can handle any request.

2. **Agent runs are background jobs** — Never block an HTTP connection for more than a few seconds. POST `/chat` enqueues a job and returns `run_id`. The frontend subscribes to events for that `run_id` via SSE backed by Redis pub/sub.

3. **HITL is a distributed protocol** — Approval requests published to Redis, resolved by any pod that receives the POST, result published back and consumed by the SSE subscriber. No futures, no queues, just Redis.

4. **Memory has a budget** — Sliding window + periodic summarisation. Never pass 100% of history to the model. Checkpoint to PostgreSQL so memory survives pod restarts.

5. **Every tool call is audited** — Write `{tool_name, input, output, latency, error, user_id, thread_id}` to a structured audit table. Essential for debugging, cost control, and compliance.

6. **Sandbox untrusted execution** — Code interpreter and web browsing run in separate pods with `NetworkPolicy` egress restrictions. The agent backend should not have direct internet access.

7. **Observability is non-negotiable** — Unified trace ID flows from browser (`traceparent` header) through Next.js → FastAPI → agent worker → tool → LLM call. Every log line contains `trace_id`. Alerting on error rate, p95 latency, token budget.

---

## 5. Prioritised Action Plan

### Sprint 1 — Make multi-replica safe (before any K8s deploy)
- [ ] Wire `RedisMemory` as default memory backend
- [ ] Replace `WebHITLBridge` internals with Redis pub/sub
- [ ] Move `TaskStore` to Redis Hash  
- [ ] Add JWT auth middleware to FastAPI  
- [ ] Store Spotify OAuth tokens in PostgreSQL (encrypted)

### Sprint 2 — Observability + hardening
- [ ] Add `python-json-logger`, structured log fields  
- [ ] Add Loki to `docker-compose.yml` + Grafana datasource  
- [ ] Forward `traceparent` from Next.js proxy to backend  
- [ ] Add rate limiting middleware (Redis token bucket)  
- [ ] Lock CORS `allow_origins` to specific domains

### Sprint 3 — Kubernetes + CI/CD
- [ ] Write `Dockerfile` for both services  
- [ ] Write K8s manifests (Deployment, Service, Ingress, HPA, Secrets)  
- [ ] GitHub Actions: test → build → push → deploy  
- [ ] Set up cert-manager for TLS  
- [ ] Configure `PodDisruptionBudget` for zero-downtime deploys

### Sprint 4 — Agent quality
- [ ] Move agent execution to ARQ background queue  
- [ ] Implement graceful shutdown (SIGTERM drain)  
- [ ] Add sliding-window memory with summarisation  
- [ ] Add model router with fallback chain  
- [ ] Add per-user token ledger and cost dashboard

---

*Generated March 2026 — re-audit after each sprint.*
