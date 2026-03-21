# Topology Scratch — Review Before Draw.io

> Work-in-progress. Agree on this first, then generate the draw.io diagram.

---

## Service Hierarchy

```
Frontend UI
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Edge                                                        │
│   API Gateway                                               │
│   (routing · rate limiting · auth verification)             │
└──────────────────┬──────────────────────────────────────────┘
                   │                         └── auth check ──►
                   │                         ┌────────────────────────────────────────────────┐
                   │                         │ Platform                                       │
                   │                         │   Auth Service      identity + policy          │
                   │                         │   MCP Gateway       external tools · routing   │
                   │                         │   Admin Console     operator controls          │
                   │                         └──────────────────────┬─────────────────────────┘
                   │                                                 │
                   ▼                                          manage │ (dashed, operator only)
┌──────────────────────────────────────────────────────────────▼────┤
│ Core Execution   ← always active, platform layer                  │
│                                                                    │
│   Live Stream          SSE / WebSocket delivery to browser        │
│   Human Gate           HITL checkpoint · pause / resume run       │
│   Notification Hub     email · Slack · webhook  (new, v2)         │
│   Conversation Service conversation threads · message history     │
│   Job Controller       durable run state · retries · crash recovery│
│          │                                                         │
│          ▼                                                         │
│   ┌──────────────────────────────────────────────────────────┐    │
│   │ Agent Runtime  ← AI execution layer, active during a run │    │
│   │                                                           │    │
│   │   Agent Engine    ReAct loop · LLM calls · tool decisions │    │
│   │          │                                                │    │
│   │          ▼                                                │    │
│   │   Tool Router     dispatch · quotas · timeouts            │    │
│   │        ├──► ┌─────────────────────────────────────────┐  │    │
│   │        │    │ gVisor RuntimeClass                     │  │    │
│   │        │    │   Code Sandbox   isolated Python        │  │    │
│   │        │    │                  session-affine per run │  │    │
│   │        │    └─────────────────────────────────────────┘  │    │
│   │        └──► File Store    uploads · extraction            │    │
│   │        └──► MCP Gateway   (external tool calls)          │    │
│   └──────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Data                                                                 │
│   PostgreSQL       durable state (threads, runs, HITL records)      │
│   Redis            session cache (live Code Sandbox bindings)        │
│   Object Storage   files and artifacts (MinIO / S3)                 │
│   Event Bus        async event fan-out (Kafka / Pulsar)             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Key Flows (to verify)

### Happy path — user sends a message
```
User → API Gateway → Conversation Service → Job Controller
       → Agent Engine (ReAct loop)
           ↓ decides to call a tool
       → Tool Router
           ├── code exec  → Code Sandbox (gVisor)
           ├── file ops   → File Store → Object Storage
           └── ext tool   → MCP Gateway → external MCP server
       → Agent Engine (loop again or done)
       → Event Bus → Live Stream → SSE → browser
```

### Human-in-the-loop (HITL)
```
Job Controller → Human Gate (run pauses, pending approval stored)
User sees approval card in browser (via Live Stream)
User approves / rejects → Human Gate → Job Controller resumes
```

### User offline — run completes
```
Agent Engine done → Event Bus → Notification Hub
→ email / Slack / webhook delivered to user
```

### Auth on every request
```
API Gateway → Auth Service (identity + policy check)
            → continue to Core Execution only if auth passes
```

### Admin operator
```
Admin Console --manage--> Job Controller (drain, cancel, diagnostics)
```

---

## Open Questions / Things to Confirm

- [ ] Is **Notification Hub** in scope for V1, or deferred to V2?
- [ ] Should **Conversation Service** also handle workspace/project scoping, or is that Auth Service's job?
- [ ] Does **Job Controller** need a dedicated queue (e.g. per-tenant Kafka topic) or is a DB-backed queue (like Postgres SKIP LOCKED) enough for V1?
- [ ] Is **MCP Gateway** call-through only, or does it also cache tool schemas locally?
- [ ] For **Code Sandbox** — session-affinity via Redis binding: what is the eviction policy when a session has been idle for N minutes?
- [ ] Should **File Store** be inside Agent Runtime, or is it a platform-level service (i.e., also used for user-uploaded files outside of an agent run)?

---

## Layers Summary

| Layer | Services | Always active? |
|---|---|---|
| **Edge** | API Gateway | ✅ Yes |
| **Platform** | Auth Service, MCP Gateway, Admin Console | ✅ Yes |
| **Core Execution** | Live Stream, Human Gate, Notification Hub, Conversation Service, Job Controller | ✅ Yes |
| **Agent Runtime** | Agent Engine, Tool Router, Code Sandbox, File Store | ⚡ Only during a run |
| **Data** | PostgreSQL, Redis, Object Storage, Event Bus | ✅ Yes |
