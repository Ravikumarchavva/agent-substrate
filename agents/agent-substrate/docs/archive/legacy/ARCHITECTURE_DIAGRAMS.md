# Agent Framework — Architecture Diagrams

Eight diagrams covering every layer of the system, from individual components to the full end-to-end request flow.

---

## 1. Top-Level Component Map

Shows every module and how they connect. The frontend talks only to FastAPI routes; routes pull all dependencies from `ServerContext` via `Depends(get_ctx)`; the agent uses Redis + Postgres for memory; the LLM is OpenAI.

```mermaid
graph TB
    subgraph Frontend["🖥️ Next.js Frontend (ai-chatbot-ui)"]
        UI[page.tsx\nChat UI]
        SSEClient[EventSource\nSSE Consumer]
        HIC[HumanInputCard]
        TAC[ToolApprovalCard]
        KB[KanbanPanel]
    end

    subgraph FastAPI["⚙️ FastAPI Server (agent-framework)"]
        subgraph Routes["server/routes/"]
            ChatRoute[POST /chat\nchat.py]
            HITLRoute[POST /chat/respond\nhitl.py]
            CancelRoute[POST /chat/cancel\ncancel.py]
            TasksRoute["GET/PATCH /tasks\ntasks.py"]
            ThreadsRoute[threads.py]
            FilesRoute["GET/POST/DELETE /files\nfiles.py"]
        end

        subgraph Context["server/context.py"]
            ServerCtx[ServerContext\nDI Container]
        end

        subgraph Services["server/services/"]
            AgentSvc[agent_service.py\nAgent factory +\nmemory restore]
            FileSvc[file_service.py\nsave / extract / purge]
        end

        subgraph SSE["server/sse/"]
            EB[EventBus\nevents.py]
            HITL[WebHITLBridge\nbridge.py]
            BR[BridgeRegistry\nbridge.py]
        end

        subgraph Agent["core/agents/"]
            ReAct[ReActAgent\nreact_agent.py]
        end

        subgraph Memory["core/memory/"]
            RM[RedisMemory]
            UBM[UnboundedMemory]
            SWC[SlidingWindowContext]
        end

        subgraph FileSystem["core/storage/"]
            FSAbC[FileStore ABC\nbase.py]
            LocalFS[LocalFileStore\nlocal.py]
            S3FS[S3FileStore\ns3.py]
            EncFS[EncryptedFileStore\nencrypted.py]
            TC[TenantContext\ntenant.py]
            FSFact[create_file_store\nfactory.py]
        end

        subgraph Tools["tools/"]
            AHT[AskHumanTool\nHITL input]
            TMT[TaskManagerTool\nKanban board]
            FMT[FileManagerTool\nagent file CRUD]
            BT[BuiltinTools\ncalculator, time, etc.]
            MCP[MCPTools\nexternal integrations]
        end
    end

    subgraph Storage["💾 Storage"]
        Redis[(Redis 7\nHot session store)]
        PG[(PostgreSQL 16\nCold / persistent store\n+ FileMetadata table)]
        ObjStore[(LocalFS volume\nor MinIO / S3\nFile object store)]
    end

    subgraph LLM["🤖 LLM Provider"]
        OAI[OpenAI API\ngpt-4o-mini]
    end

    UI -->|POST /chat| ChatRoute
    SSEClient -->|SSE stream| ChatRoute
    HIC -->|POST /chat/respond| HITLRoute
    TAC -->|POST /chat/respond| HITLRoute
    KB -->|GET /tasks| TasksRoute
    UI -->|POST /files upload| FilesRoute

    ChatRoute -->|Depends get_ctx| ServerCtx
    HITLRoute -->|Depends get_ctx| ServerCtx
    CancelRoute -->|Depends get_ctx| ServerCtx
    FilesRoute -->|Depends get_ctx| ServerCtx

    ServerCtx --> BR
    ServerCtx --> RM
    ServerCtx --> BT
    ServerCtx --> FSAbC

    ChatRoute --> AgentSvc
    AgentSvc --> ReAct
    AgentSvc --> RM

    FilesRoute --> FileSvc
    FileSvc --> FSAbC
    FileSvc --> PG

    ChatRoute --> FileSvc

    FSFact --> LocalFS
    FSFact --> S3FS
    LocalFS & S3FS --> EncFS
    FSAbC --> TC
    LocalFS -.-> ObjStore
    S3FS -.-> ObjStore

    ReAct -->|run_stream| EB
    ReAct --> OAI
    ReAct --> Tools
    ReAct --> FMT

    FMT --> FSAbC
    FMT --> PG

    EB -->|typed events| ChatRoute
    HITL -->|HITL events| ChatRoute

    BR -->|acquire/release| HITL
    HITLRoute -->|resolve| BR

    RM <--> Redis
    AgentSvc <--> PG
```

---

## 2. EventBus — How Events Flow from Agent to SSE

The `EventBus` is a **typed wrapper around `asyncio.Queue`**. Two background tasks write into it:
- `agent_worker` emits strongly-typed events (`TextDeltaEvent`, `CompletionEvent`, etc.)
- `hitl_worker` drains the bridge's outgoing queue and re-emits as `RawDictEvent`

The SSE generator polls the bus every 200 ms. On a `TimeoutError` it checks for browser disconnect or explicit cancel. When `bus.close()` is called it pushes the `BUS_CLOSED` sentinel which tells the consumer to stop.

```mermaid
graph LR
    subgraph Producers["Producers (write to bus)"]
        AW[agent_worker\nasyncio Task]
        HW[hitl_worker\nasyncio Task]
    end

    subgraph EventBus["EventBus — asyncio.Queue wrapper"]
        Q[asyncio.Queue\ntyped AgentEvent items]
        SENTINEL[_BUS_DONE sentinel\npublic alias: BUS_CLOSED]
        EMIT[emit — put typed event]
        EMITD[emit_dict — wrap as RawDictEvent]
        POLL[poll timeout 200ms\nraises TimeoutError if empty]
        CLOSE[close — push BUS_CLOSED\nidempotent]
    end

    subgraph EventTypes["Typed Events emitted by agent_worker"]
        TDE[TextDeltaEvent\nstreaming text chunk]
        RDE[ReasoningDeltaEvent\nthinking chunk]
        TCE[ToolCallEvent\ntool requested]
        TRE[ToolResultEvent\ntool completed]
        TARE[ToolApprovalRequestEvent\nwaiting approval]
        HIRE[HumanInputRequestEvent\nwaiting input]
        CE[CompletionEvent\nagent done]
        EE[ErrorEvent\nfailure]
        RDE2[RawDictEvent\nescape hatch for dicts\ntask_updated, etc.]
    end

    subgraph Consumer["Consumer — SSE generator loop in chat.py"]
        LOOP[poll loop\nevery 200 ms]
        DISC[disconnect check\nrequest.is_disconnected]
        CANCEL[cancel check\ncancel_event.is_set]
        SERIALISE[to_sse_line\nor json.dumps item.to_dict]
        SSE[yield SSE bytes\ndata: JSON]
    end

    AW -->|emit / emit_dict| EMIT
    HW -->|emit_dict for HITL events\nclose when BRIDGE_DONE| CLOSE
    EMIT --> Q
    EMITD --> Q
    CLOSE --> SENTINEL
    SENTINEL --> Q

    Q --> POLL
    POLL -->|item or TimeoutError| LOOP
    LOOP -->|TimeoutError| DISC
    LOOP -->|TimeoutError| CANCEL
    LOOP -->|BUS_CLOSED| SSE
    LOOP -->|typed event| SERIALISE
    SERIALISE --> SSE

    TDE -.-> Q
    RDE -.-> Q
    TCE -.-> Q
    TRE -.-> Q
    TARE -.-> Q
    HIRE -.-> Q
    CE -.-> Q
    EE -.-> Q
    RDE2 -.-> Q
```

---

## 3. WebHITLBridge — HITL Approval Sequence

`WebHITLBridge` is a **two-way async channel**. The agent blocks on an `asyncio.Future`. The future's ID is broadcast over SSE to the frontend which shows a UI card. When the user clicks Approve/Deny it POSTs to `/chat/respond/{requestId}` which calls `future.set_result()` to unblock the agent. On disconnect, `cancel_all_pending()` settles all futures immediately so the agent never hangs.

```mermaid
sequenceDiagram
    participant Agent as ReActAgent
    participant Bridge as WebHITLBridge
    participant HW as hitl_worker Task
    participant SSE as SSE Generator
    participant FE as Frontend Browser
    participant HIEP as POST /chat/respond

    Note over Agent,FE: Normal HITL approval flow (BLOCKING mode)

    Agent->>Bridge: tool needs approval\n_handle_approval(request)
    Bridge->>Bridge: create asyncio.Future\nstore in _pending[request_id]
    Bridge->>Bridge: save payload in _pending_payloads
    Bridge->>Bridge: _outgoing.put({type: tool_approval_request, ...})
    Note over Agent: Agent is BLOCKED\nawait asyncio.wait_for(future, 300s)

    HW->>Bridge: get_event() — pulls from _outgoing
    HW->>SSE: bus.emit_dict({type: tool_approval_request})
    SSE->>FE: data: {"type":"tool_approval_request","requestId":"..."}

    FE->>FE: render ToolApprovalCard
    FE->>HIEP: POST /chat/respond/{requestId}\n{"action":"approve"}

    HIEP->>Bridge: BridgeRegistry.resolve(requestId, data)
    Bridge->>Bridge: future.set_result({"action":"approve"})
    Note over Agent: Agent UNBLOCKS\nreceives ToolApprovalResponse

    Agent->>Agent: executes tool\ncontinues ReAct loop

    Note over Agent,FE: Browser disconnect while HITL pending

    FE--xSSE: TCP disconnect
    SSE->>SSE: poll() → TimeoutError\nrequest.is_disconnected() = True
    SSE->>Bridge: cancel_all_pending("session_disconnected")
    Bridge->>Bridge: future.set_result({session_disconnected: True})
    Note over Agent: Agent UNBLOCKS\ngets session_disconnected → DENY
    SSE->>SSE: bridge.signal_done()\nagent_task.cancel()

    Note over Bridge: Bridge KEPT ALIVE in BridgeRegistry\n(has_pending was True during the run)\nUser can reconnect and POST respond later
```

---

## 4. ReActAgent — ReAct Loop Internals

Shows the full Think → Act → Observe loop: LLM generates text/tool calls, each tool either runs directly or goes through the approval handler, results feed back into memory, and the loop repeats until `finish_reason=stop` or `max_iterations` is hit.

```mermaid
flowchart TD
    START([run_stream called\nwith user_content]) --> SEED

    SEED[seed system message\nif memory empty] --> IGSEED

    IGSEED[run input guardrails\nMaxTokenGuardrail etc.] --> ADDUSER

    ADDUSER[memory.add_message\nUserMessage] --> LLM

    LLM[call LLM\nmodel_client.generate_stream\nwith SlidingWindowContext] --> STREAM

    STREAM{LLM response\ntype?}

    STREAM -->|TextDelta chunk| TXT[yield TextDeltaChunk\nto caller]
    TXT --> STREAM

    STREAM -->|ReasoningDelta chunk| RSN[yield ReasoningDeltaChunk\nto caller]
    RSN --> STREAM

    STREAM -->|CompletionChunk\nfinish_reason=stop| DONE_NO_TOOLS

    STREAM -->|CompletionChunk\nfinish_reason=tool_calls| PARSE

    DONE_NO_TOOLS[yield CompletionChunk\nmemory.add_message AssistantMessage\nrun output guardrails] --> RETURN

    PARSE[parse tool calls\nnormalise to _ParsedToolCall list] --> ADDASSIST

    ADDASSIST[memory.add_message\nAssistantMessage with tool_calls] --> ITERTOOLS

    ITERTOOLS[for each tool call...] --> APPROVAL

    APPROVAL{tool in\ntools_requiring_approval?}

    APPROVAL -->|yes| HITL_CHECK[call tool_approval_handler\nwait for future\nblocking / timeout / fire-and-continue]

    HITL_CHECK -->|DENY| SKIP[emit synthetic\nTool Denied result]
    HITL_CHECK -->|APPROVE may modify args| EXEC

    APPROVAL -->|no| EXEC

    EXEC[execute tool\ntool.execute with timeout\ntool_retry_policy on error] --> RESULT

    RESULT[yield ToolExecutionResultMessage\nmemory.add_message result] --> NEXTCALL

    NEXTCALL{more tool calls\nin this iteration?}
    NEXTCALL -->|yes| APPROVAL
    NEXTCALL -->|no| CHECKITER

    SKIP --> NEXTCALL

    CHECKITER{iterations <\nmax_iterations?}
    CHECKITER -->|yes, loop again| LLM
    CHECKITER -->|no, max hit| FORCESTOP[yield ErrorEvent\nmax iterations reached]

    RETURN([AgentRunResult\nwith all steps])
    FORCESTOP --> RETURN
```

---

## 5. Memory System — Redis Hot Path + PostgreSQL Cold Store

On every request, `RedisMemory.restore()` tries Redis first and loads **all** stored messages into the local in-process list. On a miss it reads from Postgres and seeds Redis. During the run, every `add_message()` does a **fire-and-forget background `RPUSH`** to Redis — no blocking writes. `SlidingWindowContext` is the *only* layer that limits history — it selects the last `model_context_window` messages at LLM-call time. Postgres is written synchronously only for `CompletionChunk` and `ToolResultMessage` (inline, before the SSE event is emitted).

```mermaid
flowchart TD
    subgraph Request["Per-Request Flow in agent_service.py"]
        START([load_agent_for_thread called]) --> TRY_REDIS

        TRY_REDIS[per_request_mem = RedisMemory.for_session\nshares parent connection pool] --> RESTORE

        RESTORE[await per_request_mem.restore\nloads ALL messages from Redis\nno limit -- full history] --> HIT?

        HIT?{Redis hit?\nmessages found?}

        HIT? -->|yes, hot path| HOT[use per_request_mem directly\nfull history in local list\nalready loaded]

        HIT? -->|no, cold path| COLD[load_messages_for_memory\nfetch ordered steps from PostgreSQL]

        COLD --> REBUILD[_rebuild_messages\nmap step rows to message objects\nSystemMessage UserMessage\nAssistantMessage ToolExecutionResultMessage]

        REBUILD --> SEED_REDIS[add all messages to RedisMemory\nseeds Redis from Postgres cold store]

        SEED_REDIS --> HOT

        HOT --> CTX[wrap in SlidingWindowContext\nmax_messages = context_window e.g. 40\nfilters at LLM-call time -- LLM sees last N\nfull history kept in local list + Redis]

        CTX --> AGENT[create ReActAgent\nwith per_request_mem + SlidingWindowContext\nall add_message calls write-through\nto Redis via fire-and-forget task]
    end

    subgraph WriteThrough["Write-Through During Agent Run"]
        AT[agent.run_stream\nadd_message on every step] --> ADD

        ADD[RedisMemory.add_message\nappends to local list\nschedules background task] --> BG

        BG[asyncio background task\nRPUSH to Redis\nLTRIM to max_messages cap\nEXPIRE TTL refresh]
    end

    subgraph PersistRun["Inline DB Persistence in agent_worker"]
        COMP[CompletionChunk received\nby agent_worker] --> PCOMP

        PCOMP[persist_assistant_message\nPOSTGRES upsert\nBEFORE yielding SSE event]

        TR[ToolExecutionResultMessage\nreceived] --> PTR

        PTR[persist_tool_result\nPOSTGRES insert\nBEFORE yielding SSE event]
    end

    AGENT --> AT
    AGENT --> COMP
    AGENT --> TR

    Redis[("Redis 7\nLIST per session_id\nkey: session:ID:messages\ncap: max_messages e.g. 200\nttl: REDIS_SESSION_TTL")] -.-> RESTORE
    BG -.-> Redis
    PG[(PostgreSQL 16\nsteps table per thread)] -.-> COLD
    PCOMP -.-> PG
    PTR -.-> PG
```

---

## 6. ServerContext — DI Container, Locks and Cancel Registry

`ServerContext` is the single DI container assembled at startup. The `thread_locks` dict prevents concurrent streams on the same thread (returns 409). The `cancel_registry` dict maps thread IDs to `asyncio.Event`s; the cancel route sets the event and the poll loop detects it within the next 200 ms poll window.

```mermaid
graph TB
    subgraph AppLifecycle["FastAPI Lifespan — app.py startup"]
        OTEL[configure OpenTelemetry\nOTLP → Tempo]
        DBInit[init_db\nSQLAlchemy async engine]
        RedisInit[RedisMemory global\nconnect TCP pool]
        FSInit[create_file_store\nLocalFileStore or S3FileStore\n+ optional EncryptedFileStore]
        ModelClient[OpenAIClient\ngpt-4o-mini]
        AudioClient[OpenAIAudioClient\ntranscription + TTS + realtime]
        BRInit[BridgeRegistry\nper-thread HITL bridge pool]
        ToolReg[ToolRegistry\nregister all tools at startup]
        CTX_BUILT[build ServerContext\nall deps bundled]
        STATE[app.state.ctx = ctx]
    end

    subgraph ServerContext["ServerContext dataclass — context.py"]
        SC[ServerContext]
        SC --> MC[model_client\nOpenAIClient]
        SC --> AC[audio_client\nOpenAIAudioClient]
        SC --> RM2[redis_memory\nglobal RedisMemory]
        SC --> TR2[tools\nToolRegistry]
        SC --> BR2[bridge_registry\nBridgeRegistry]
        SC --> TRA[tools_requiring_approval\nlist of tool names]
        SC --> SI[system_instructions\nloaded from prompts/default_system.md]
        SC --> TT[tool_timeout float]
        SC --> CR[cancel_registry\ndict thread_id → asyncio.Event]
        SC --> TL[thread_locks\ndict thread_id → asyncio.Lock]
        SC --> SF[session_factory\nSQLAlchemy async factory]
        SC --> MCP2[mcp_servers\ndict name → config]
        SC --> CI[ci_client\nCodeInterpreterClient optional]
        SC --> FS2[file_store\nFileStore ABC\nLocalFileStore or S3FileStore]
    end

    subgraph SingleFlight["Single-Flight Lock — thread_locks"]
        TL --> SFL[setdefault: create Lock if missing]
        SFL --> LOCKED{locked?}
        LOCKED -->|yes| HTTP409[raise 409\nstream already running]
        LOCKED -->|no| ACQUIRE[acquire lock\nfor SSE generator lifetime]
        ACQUIRE --> FINREL[finally: release lock\npop from dict on stream end]
    end

    subgraph CancelReg["Cancel Registry — cancel_registry"]
        CR --> CRK[key: str thread_id]
        CRK --> EVT[value: asyncio.Event]
        EVT --> CPOST[POST /chat/cancel\n→ event.set]
        CPOST --> POLLED[poll loop detects\ncancel_event.is_set]
        POLLED --> CANYIELD[yield cancelled SSE]
    end

    OTEL & DBInit & RedisInit & FSInit & ModelClient & AudioClient & BRInit & ToolReg --> CTX_BUILT --> STATE

    subgraph DI["Dependency Injection — get_ctx"]
        GETCTX["def get_ctx(request: Request)\n→ request.app.state.ctx"]
    end

    STATE -.-> GETCTX
    GETCTX -.->|"Depends(get_ctx) in every route"| SC
```

---

## 7. Complete End-to-End Flow

The full lifecycle of a user message across all 7 phases: request setup → memory restore → file context build → direct text response → tool call → HITL approval → cancel/disconnect.

> **EventBus** is an in-process `asyncio.Queue` — not a network service. It is not shown as a participant; instead the agent_worker emits events to it and the SSE generator polls it, both inside the same process.

```mermaid
sequenceDiagram
    autonumber
    participant FE as Frontend\n(page.tsx)
    participant ChatAPI as POST /chat\n(chat.py)
    participant AgSvc as agent_service\nload_agent_for_thread
    participant Redis as Redis 7\nHot Store
    participant PG as PostgreSQL\nCold Store
    participant FileStore as FileStore\n(local / S3)
    participant ReAct as ReActAgent\nreact_agent.py
    participant OAI as OpenAI API
    participant Tools as Tools\n(executor)
    participant Bridge as WebHITLBridge\nhitl.py
    participant HIRE as POST /chat/respond\n(hitl.py)

    Note over FE,HIRE: ═══ STARTUP (once) ═══
    Note over ChatAPI: app.state.ctx = ServerContext\n(model_client, bridge_registry,\nredis_memory, tools, file_store, locks, ...)

    Note over FE,HIRE: ═══ PHASE 1 — Request Setup ═══

    FE->>ChatAPI: POST /chat {thread_id, messages[]}
    ChatAPI->>PG: get_thread(thread_id) — validate exists
    ChatAPI->>ChatAPI: thread_locks.setdefault(thread_id, Lock)\nif locked → 409 Conflict
    ChatAPI->>ChatAPI: thread_lock.acquire()
    ChatAPI->>Bridge: bridge_registry.acquire(thread_id)\nget or create WebHITLBridge
    ChatAPI->>AgSvc: load_agent_for_thread(thread_id, ...)

    Note over FE,HIRE: ═══ PHASE 2 — Memory Restore ═══

    AgSvc->>Redis: RedisMemory.restore()\nLRANGE session:{id}:messages 0 -1
    alt Redis HIT (hot path)
        Redis-->>AgSvc: ALL stored messages (up to max_messages cap)
        Note over AgSvc: full history loaded into local in-process list
    else Redis MISS (cold path)
        Redis-->>AgSvc: empty
        AgSvc->>PG: load_messages_for_memory(thread_id)\nSELECT steps ORDER BY created_at
        PG-->>AgSvc: step rows
        AgSvc->>AgSvc: _rebuild_messages(rows)\nSystemMessage + UserMessage +\nAssistantMessage + ToolResultMessage
        AgSvc->>Redis: add_message for each — seeds Redis from Postgres
    end
    AgSvc->>AgSvc: wrap in SlidingWindowContext(max=model_context_window)\nfilters at LLM-call time — last N msgs sent to OpenAI\nfull history stays in local list and Redis
    AgSvc-->>ChatAPI: ReActAgent ready

    Note over FE,HIRE: ═══ PHASE 3 — File Context + Persist Setup ═══

    ChatAPI->>PG: get_files_by_ids(file_ids) — query FileMetadata
    PG-->>ChatAPI: FileMetadata records
    ChatAPI->>FileStore: extract_text(store, meta) per attached file
    FileStore-->>ChatAPI: extracted text bytes
    ChatAPI->>ChatAPI: hooks.fire_message(hook_ctx, user_content)
    ChatAPI->>PG: persist_user_message(thread_id, content)
    Note over ChatAPI: EventBus() — create fresh in-process asyncio.Queue\nasyncio.create_task(agent_worker)\nasyncio.create_task(hitl_worker)
    ChatAPI-->>FE: StreamingResponse (SSE)\nContent-Type: text/event-stream

    Note over FE,HIRE: ═══ PHASE 4 — Direct Response (no tools) ═══

    activate ChatAPI
    ChatAPI->>ReAct: agent.run_stream(user_content)
    ReAct->>Redis: memory.add_message(UserMessage)\nfire-and-forget background RPUSH
    ReAct->>OAI: generate_stream(messages, tools)\nSlidingWindowContext builds prompt
    OAI-->>ReAct: TextDelta chunks...
    ReAct-->>ChatAPI: yield TextDeltaChunk
    Note over ChatAPI: agent_worker emits event to in-process EventBus\nSSE generator polls bus and streams to FE
    ChatAPI-->>FE: data: {"type":"text_delta","content":"Hello..."}

    OAI-->>ReAct: CompletionChunk finish_reason=stop
    ReAct->>Redis: memory.add_message(AssistantMessage)
    ReAct-->>ChatAPI: yield CompletionChunk
    ChatAPI->>PG: persist_assistant_message BEFORE emitting
    ChatAPI-->>FE: data: {"type":"completion","content":"..."}

    ChatAPI->>Bridge: agent_worker finally: bridge.signal_done() — BRIDGE_DONE sentinel
    Bridge-->>ChatAPI: hitl_worker gets BRIDGE_DONE → bus.close()
    ChatAPI-->>FE: data: [DONE]
    ChatAPI->>ChatAPI: finally: release lock\npop cancel_registry\nbridge_registry.release_if_idle

    Note over FE,HIRE: ═══ PHASE 5 — Tool Call Path ═══

    OAI-->>ReAct: CompletionChunk finish_reason=tool_calls\n[{name:"calculator", arguments:{...}}]
    ReAct->>ReAct: parse tool calls → _ParsedToolCall
    ReAct->>ReAct: memory.add_message(AssistantMessage with tool_calls)

    alt Tool NOT in tools_requiring_approval
        ReAct->>Tools: tool.execute(**arguments)
        Tools-->>ReAct: ToolResult(content="42")
        ReAct->>Redis: memory.add_message(ToolExecutionResultMessage)
        ReAct-->>ChatAPI: yield ToolExecutionResultMessage
        ChatAPI->>PG: persist_tool_result BEFORE emitting
        ChatAPI-->>FE: data: {"type":"tool_result","tool_name":"calculator","result":"42"}
    end
    ReAct->>OAI: next LLM call with tool result in context

    Note over FE,HIRE: ═══ PHASE 6 — HITL Tool Approval Path ═══

    OAI-->>ReAct: CompletionChunk finish_reason=tool_calls\n[{name:"delete_file", arguments:{...}}]
    Note over ReAct: delete_file IS in tools_requiring_approval

    ReAct->>Bridge: tool_approval_handler._handle_approval(ToolApprovalRequest)
    Bridge->>Bridge: create asyncio.Future()\n_pending[request_id] = future
    Bridge->>Bridge: _outgoing.put({type: tool_approval_request, ...})
    Note over ReAct: BLOCKED — awaiting future.set_result

    activate Bridge
    ChatAPI->>Bridge: hitl_worker: get_event()
    Bridge-->>ChatAPI: {type: tool_approval_request, requestId: "abc"}
    ChatAPI-->>FE: data: {"type":"tool_approval_request","requestId":"abc"}
    FE->>FE: render ToolApprovalCard

    FE->>HIRE: POST /chat/respond/abc\n{"action":"approve"}
    HIRE->>Bridge: BridgeRegistry.resolve("abc", {action:"approve"})
    Bridge->>Bridge: future.set_result({"action":"approve"})
    deactivate Bridge
    Note over ReAct: UNBLOCKED — returns ToolApprovalResponse APPROVE

    ReAct->>Tools: tool.execute(**arguments)
    Tools-->>ReAct: ToolResult
    ReAct-->>ChatAPI: yield ToolExecutionResultMessage
    ChatAPI-->>FE: data: {"type":"tool_result",...}

    Note over FE,HIRE: ═══ PHASE 7 — Cancel / Disconnect ═══

    FE->>ChatAPI: POST /chat/{thread_id}/cancel
    ChatAPI->>ChatAPI: cancel_registry[thread_id].set()
    Note over ChatAPI: poll() → TimeoutError\ncancel_event.is_set() = True
    ChatAPI->>ReAct: agent_task.cancel()
    ChatAPI-->>FE: data: {"type":"cancelled"}
    ChatAPI-->>FE: data: [DONE]
    deactivate ChatAPI
```

---

## 8. File Storage Architecture

Four-zone view of the file storage subsystem: API ingress → service functions → storage abstraction → persistence backends.

```mermaid
flowchart TD
    subgraph APILayer["🌐 API Layer"]
        FilesRoute["FilesRoute\nPOST /files (upload)\nGET /files (list)\nGET /files/{id} (download)\nDELETE /files/{id}"]
        FMT["FileManagerTool\nagent-facing CRUD\ncurrent_thread_id ContextVar"]
    end

    subgraph ServiceLayer["⚙️ Service Layer — file_service.py"]
        SaveFile["save_file()\nwrite bytes → store\ninsert FileMetadata row"]
        ListFiles["list_files()\nSELECT FileMetadata\nby thread / user / org"]
        GetFile["get_file()\nfetch bytes from store\nchecks ownership"]
        DeleteFile["delete_file()\ndelete from store\ndelete FileMetadata row"]
        ExtractText["extract_text()\nplain-text → raw bytes\nPDF/image → placeholder"]
        GetURL["get_file_url()\npresigned URL (S3)\nor local /files/{id}"]
        PurgeThread["purge_thread_files()\ndelete all files for\na thread on cleanup"]
    end

    subgraph StorageLayer["🗂️ Storage Layer — core/storage/"]
        FSAbC["FileStore ABC\nput / get / delete / copy\nlist / exists / make_public_url"]
        LocalFS["LocalFileStore\nlocal disk volume\npath = base_dir/{tenant_path}"]
        S3FS["S3FileStore\naiobotocore async\nbucket/{tenant_path}"]
        EncFS["EncryptedFileStore\nAES-256-GCM wrapper\ntransparent encrypt/decrypt on put/get"]
        TC["TenantContext path builder\norg/{org_id}/user/{user_id}/\nthread/{thread_id}/{scope}/"]
        Factory["create_file_store(settings)\ndispatches on FILE_STORE_BACKEND\n+ wraps EncryptedFileStore\nif FILE_STORE_ENCRYPT=true"]
    end

    subgraph PersistenceLayer["💾 Persistence Layer"]
        PGMeta[("PostgreSQL\nFileMetadata table\nid, thread_id, user_id, org_id\nstore_key, filename, mime_type\nsize_bytes, created_at")]
        ObjStore[("LocalFS volume\nor MinIO / AWS S3\nraw file bytes")]
    end

    FilesRoute --> SaveFile & ListFiles & GetFile & DeleteFile & GetURL
    FMT --> SaveFile & ListFiles & GetFile & DeleteFile

    SaveFile & GetFile & DeleteFile & ExtractText & GetURL & PurgeThread --> FSAbC
    SaveFile & ListFiles & GetFile & DeleteFile --> PGMeta

    FSAbC --> LocalFS & S3FS
    LocalFS & S3FS --> EncFS
    Factory --> LocalFS & S3FS
    FSAbC --> TC

    LocalFS --> ObjStore
    S3FS --> ObjStore
```
