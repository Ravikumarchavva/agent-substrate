# Execution Pipeline

End-to-end trace of a user message through the Agent Framework — from browser keystroke
to streamed SSE token — with component-level box boundaries.

---

## Component Boundaries

| Box | Components inside |
|---|---|
| **Browser** | React UI (`page.tsx`), EventSource SSE consumer |
| **Next.js BFF** | `app/api/chat/route.ts`, `app/api/chat/respond/[id]/route.ts` |
| **FastAPI Server** | `server/app.py`, `server/routes/chat.py`, `server/routes/workflows.py`, `WebHITLBridge` |
| **Restate Cluster** | Restate ingress (HTTP), durable journal, promise store |
| **Worker** | `integrations/runtime/restate/worker.py`, `activities.py`, `ReActAgent` loop |
| **Infrastructure** | Redis (conversation memory), PostgreSQL (thread/message persistence), NATS (pub/sub fan-out), OpenAI API |

---

## Normal Execution Flow

```mermaid
sequenceDiagram
    box Browser
        participant UI as React UI
        participant SSE as EventSource
    end

    box Next.js BFF
        participant BFF as /api/chat route
        participant HITLProxy as /api/chat/respond
    end

    box FastAPI Server
        participant API as FastAPI
        participant Bridge as WebHITLBridge
        participant WFClient as RestateWorkflowClient
    end

    box Restate Cluster
        participant Restate as Restate Ingress
        participant Journal as Durable Journal
    end

    box Worker
        participant Worker as Worker Process
        participant Act as Activities
        participant Agent as ReActAgent
    end

    box Infrastructure
        participant Redis as Redis
        participant PG as PostgreSQL
        participant NATS as NATS
        participant LLM as OpenAI API
    end

    UI->>BFF: POST /api/chat {message, threadId}
    BFF->>API: POST /chat (proxied)
    API->>Bridge: register SSE channel (conversationId)
    API->>WFClient: start_agent_workflow(conv_id, message)
    WFClient->>Restate: POST /invoke/AgentWorkflow/{conv_id}/agent_run
    Restate-->>WFClient: 200 OK (workflow accepted)
    WFClient-->>API: AgentWorkflowHandle
    API-->>BFF: 200 + SSE stream open
    BFF-->>SSE: SSE stream open
    SSE-->>UI: stream connected

    Restate->>Worker: dispatch agent_run(input)
    Worker->>Act: configure() [already done at startup]
    Act->>Redis: restore_memory(conv_id)
    Redis-->>Act: message history

    loop ReAct Loop
        Act->>LLM: do_llm_call(messages)
        LLM-->>Act: AssistantMessage (text or tool_use)
        Journal->>Journal: persist llm result

        alt stop_reason == tool_use
            Act->>Act: do_tool_exec(tool_name, args)
            Journal->>Journal: persist tool result
            Act->>Redis: persist_message(tool_result)
            Act->>PG: persist_message(tool_result)
        end

        Act->>NATS: publish_event(text_delta / tool_call / tool_result)
        NATS->>Bridge: fan-out to SSE channel
        Bridge-->>SSE: SSE event
        SSE-->>UI: render token / tool bubble
    end

    Act->>NATS: publish_event(completion)
    NATS->>Bridge: fan-out
    Bridge-->>SSE: completion event
    SSE-->>UI: finalize assistant turn
```

---

## HITL (Human-In-The-Loop) Approval Flow

Triggered when a tool has `requires_approval=True` or the agent calls `ask_human`.

```mermaid
sequenceDiagram
    box Browser
        participant UI as React UI
        participant SSE as EventSource
    end

    box Next.js BFF
        participant HITLProxy as /api/chat/respond/[id]
    end

    box FastAPI Server
        participant API as FastAPI /hitl/respond
        participant WFClient as RestateWorkflowClient
    end

    box Restate Cluster
        participant Restate as Restate Ingress
        participant Promise as Durable Promise Store
    end

    box Worker
        participant Act as Activities
    end

    Act->>Promise: ctx.promise("hitl-{requestId}")
    Act->>Act: publish_event(tool_approval_request / human_input_request)
    Note over Act: worker suspends — thread freed

    Act-->>SSE: SSE: tool_approval_request {requestId, tool_name, input}
    SSE-->>UI: render ToolApprovalCard / HumanInputCard

    UI->>HITLProxy: POST /api/chat/respond/{requestId} {approved, value}
    HITLProxy->>API: POST /hitl/respond/{requestId}
    API->>WFClient: resolve_promise(AgentWorkflow, conv_id, "hitl-{id}", value)
    WFClient->>Restate: POST /invoke/AgentWorkflow/{conv_id}/resolve_approval
    Restate->>Promise: resolve("hitl-{requestId}", value)
    Note over Restate: workflow resumes

    Restate->>Act: return resolved value to awaiting ctx.promise().get()
    Act->>Act: continue tool execution with approved inputs
```

---

## HITL Human Input Flow

Identical to approval above but triggered by the `ask_human` tool.

```mermaid
sequenceDiagram
    box Browser
        participant UI as React UI
    end

    box Next.js BFF
        participant HITLProxy as /api/chat/respond/[id]
    end

    box FastAPI Server
        participant API as FastAPI /hitl/respond
        participant WFClient as RestateWorkflowClient
    end

    box Restate Cluster
        participant Promise as Durable Promise Store
    end

    box Worker
        participant Act as Activities
    end

    Act->>Promise: ctx.promise("hitl-{requestId}") [ask_human variant]
    Act-->>UI: SSE: human_input_request {requestId, prompt, options}
    UI-->>UI: render HumanInputCard

    UI->>HITLProxy: POST /api/chat/respond/{requestId} {response: "user typed answer"}
    HITLProxy->>API: POST /hitl/respond/{requestId}
    API->>WFClient: resolve_promise(..., "hitl-{id}", {response: "..."})
    WFClient->>Promise: resolve
    Promise-->>Act: ctx.promise().get() returns user answer
    Act->>Act: feed user answer back into ReAct loop as ToolExecutionResultMessage
```

---

## Pipeline Workflow Execution

```mermaid
sequenceDiagram
    box FastAPI Server
        participant API as FastAPI
        participant WFClient as RestateWorkflowClient
    end

    box Restate Cluster
        participant Restate as Restate Ingress
        participant Journal as Durable Journal
    end

    box Worker
        participant PW as PipelineWorkflow
        participant Act as Activities
    end

    box Infrastructure
        participant Adapter as External Adapters
    end

    API->>WFClient: start_pipeline_workflow(id, steps, context)
    WFClient->>Restate: POST /invoke/PipelineWorkflow/{id}/pipeline_run
    Restate->>PW: dispatch pipeline_run(steps)

    loop For each step
        PW->>Act: execute_adapter_step(step, resolved_inputs)
        Note over PW,Act: $prev.field / $step[n].field refs resolved
        Act->>Adapter: call adapter (web_surfer / code_interpreter / email_sender …)
        Adapter-->>Act: step result
        Journal->>Journal: persist step result
        Act-->>PW: step_output
    end

    PW-->>Restate: pipeline complete
    Restate-->>WFClient: workflow finished
```

---

## Startup Sequence (Worker)

```mermaid
sequenceDiagram
    box Worker
        participant Main as worker.py main()
        participant Act as activities.configure()
    end

    box Infrastructure
        participant Redis as Redis
        participant PG as PostgreSQL
        participant NATS as NATS
        participant LLM as OpenAI (client init)
    end

    box Restate Cluster
        participant Admin as Restate Admin API
    end

    Main->>LLM: OpenAIClient(api_key, model)
    Main->>Redis: RedisMemory.connect()
    Main->>PG: engine connect (asyncpg)
    Main->>NATS: NATSBridge.connect()
    Main->>Act: configure(streaming, model_client, tools, redis_memory, …)
    Main->>Admin: register_deployment(worker_url)
    Admin-->>Main: 200 OK — Restate knows activity endpoints
    Note over Main: Worker ready — polling Restate for workflow tasks
```

---

## Key Data Flows Summary

```
User keystroke
  → Next.js BFF (POST /api/chat)
    → FastAPI (register SSE, start workflow)
      → RestateWorkflowClient (HTTP POST to Restate ingress)
        → Restate (persist in journal, dispatch)
          → Worker (run ReAct loop via Activities)
            → Redis  (memory restore/persist)
            → OpenAI (LLM call — result journalled)
            → Tool   (execute — result journalled)
            → PG     (persist message)
            → NATS   (publish SSE event)
              → WebHITLBridge (fan-out)
                → EventSource (Browser SSE stream)
                  → React UI (render token)
```
