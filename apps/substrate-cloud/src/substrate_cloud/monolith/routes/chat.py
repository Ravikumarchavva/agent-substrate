"""Chat streaming endpoint with HITL support.

POST /chat – send a message, receive SSE stream of agent response
including tool approval requests, human input requests, and tool results.

Intent-routing heuristics, wire-event helpers, and per-request dependency
assembly live in ``chat_intents.py``, ``chat_wire.py``, and
``chat_context.py`` respectively — this module holds only the two route
handlers.
"""

from __future__ import annotations

import logging

import json
import substrate
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.shared.settings import settings
from substrate.integrations.llm.factory import (
    CHAT_MODEL_FALLBACKS,
    create_model_client,
    detect_provider,
    has_provider_api_key,
    model_supports_vision,
    resolve_model_for_available_credentials,
    resolve_vision_model_for_available_credentials,
    strip_provider_prefix,
)
from substrate_cloud.factory import build_agent_for_thread

# ContextVar that scopes TaskManagerTool to the active thread
from substrate.types import ChatMessage as _ChatMessage, Role
from substrate.types import Actor as _Actor
from substrate.models.protocols import ReasoningEffort
from substrate.runtime import ChatPayload as _ChatPayload, Message as _Message
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.hooks import ChatContext, hooks
from substrate_cloud.monolith.schemas import ChatRequest
from substrate_cloud.monolith.services import get_owned_thread
from substrate_cloud.monolith.services.agents.service import (
    agent_instructions_block,
    get_owned_agent,
    narrow_tools,
)
from substrate_cloud.monolith.services.agents.delegation import (
    TOOL_NAME,
    AskAgentTool,
    contacts_for,
    other_agents,
)
from substrate_cloud.monolith.services.groups.drives import drives_of, run_metadata
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.sse.bridge import WebHITLBridge
from substrate_cloud.shared.rate_limit import rate_limit
from substrate_cloud.shared.doc_quota import check_and_increment, seconds_until_reset
from substrate.server.protocol import PROTOCOL_VERSION, HelloEvent
from substrate_cloud.stream import AgentStreamSession, sse_lines, tail_wire_events

from substrate_cloud.monolith.routes.chat_intents import (
    _tool_name,
    _should_allow_task_planning,
    _should_force_task_planning,
    _configure_workspace_mail_request,
    _configure_calendar_write_request,
    existing_task_board_block,
    readonly_kb_block,
    attachments_block,
    custom_instructions_block,
)
from substrate_cloud.monolith.routes.preferences import (
    instructions_for,
    load_preferences,
)
from substrate_cloud.monolith.routes.chat_wire import build_user_blocks
from substrate_cloud.monolith.routes.chat_context import (
    _get_agent_deps,
    _build_file_context,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["chat"],
    dependencies=[Depends(rate_limit), Depends(get_current_user)],
)


def _knowledge_tool_for(
    tools: list, knowledge: Any, tenant_id: str, knowledge_base: str
) -> list:
    """``tools`` with the ``knowledge`` tool pointed at ``knowledge_base``, one of the caller's own tenant's bases (the collection is built
    from the token's tenant, so no id can reach another tenant's). A base id that is not a valid id leaves the tools as they were."""
    from substrate.documents import DocumentsTool
    from substrate_cloud.documents_library import knowledge_collection_for

    collection = knowledge_collection_for(tenant_id, knowledge_base)
    if collection is None:
        return tools
    chosen = DocumentsTool(
        knowledge,
        collection=lambda scope: collection,
        name="knowledge",
        description=(
            "Search and read this assistant's knowledge base. find(query) is the way in — it searches by meaning and by words; "
            "outline and read open what it finds; view shows a figure. Cite what you read as [n] with its page."
        ),
    )
    return [t for t in tools if _tool_name(t) != "knowledge"] + [chosen]


def plan_quota_key(user: AuthClaims) -> str:
    """Redis key suffix for the daily plan-message quota.

    ``tenant_id`` when set to a real project (a project-scoped deployed
    chatbot — every visitor, anonymous or logged-in, shares one quota so a
    project's BYOK key/plan governs total usage under it, not each visitor
    individually). Falls back to ``sub`` when ``tenant_id`` is unset
    (``AuthClaims``'s own default — a real JWT always carries one now, see
    ``verify_token``, but ``AuthClaims`` built directly in-process, e.g. a
    single-user test-chat, may not).
    """
    return user.tenant_id if user.tenant_id else user.sub


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Stream agent response as Server-Sent Events with HITL support.

    Flow:
      1. Validate thread exists and belongs to the caller
      2. Single-flight check — 409 if same thread already has a running stream
      3. Build agent with restored memory + per-thread HITL bridge
      4. Fire on_message hook, persist user message
      5. Stream response via EventBus (typed events: text_delta, completion,
         tool_result, HITL events, error)
      6. Persist assistant messages and tool results inline as they arrive
    """
    runtime = ctx.runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")

    # 1. Validate thread + ownership (404 on foreign threads — no existence leak)
    thread = await get_owned_thread(db, body.thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # 1a. An agent profile, if the conversation is with one: its role and instructions, its tool permissions and its own workspace.
    thread_agent = (
        await get_owned_agent(db, thread.agent_id, user) if thread.agent_id else None
    )
    # Its own folder is /workspace, and each group it is in is a shared folder beside it.
    drives = await drives_of(db, thread_agent.id) if thread_agent is not None else ()

    # 1b. A file deleted from this thread's storage (routes/workspace.py::
    # delete_file, routes/files.py::delete_file) locks it read-only — the
    # agent shouldn't reply as if a now-missing file still exists.
    if thread.locked_at is not None:
        raise HTTPException(
            status_code=423,
            detail=thread.locked_reason or "This conversation is locked.",
        )

    # 2. Single-flight: only one active stream per thread at a time (enforced
    # durably across replicas by a unique index in the runtime store)
    if await runtime.active_run_for_thread(str(body.thread_id)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"A stream is already running for thread {body.thread_id}. "
                "Cancel it first via POST /chat/{thread_id}/cancel."
            ),
        )

    # 2b. Plan-derived daily message quota (when present in token claims)
    if user.daily_message_limit is not None:
        quota_key = plan_quota_key(user)
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            allowed, remaining = await check_and_increment(
                redis, "planquota:message", quota_key, limit=user.daily_message_limit
            )
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Daily message limit ({user.daily_message_limit}) reached. "
                        f"Resets in {seconds_until_reset()} seconds."
                    ),
                    headers={"Retry-After": str(seconds_until_reset())},
                )

    # 3. Build agent with restored memory + per-thread HITL bridge
    try:
        deps = await _get_agent_deps(ctx, str(body.thread_id))
        (
            file_block,
            image_inputs,
            attachments,
            new_attachments,
        ) = await _build_file_context(
            db,
            body,
            request,
            ctx,
            user,
        )
        if not body.messages:
            raise HTTPException(status_code=422, detail="messages[] must not be empty")
        display_content = body.messages[-1].content

        # An agent thinks with its own model in a conversation with it, whatever the composer's picker says.
        selected_model = (
            (thread_agent.model if thread_agent is not None else None)
            or body.model
            or getattr(request.app.state, "chat_model", "")
        ).strip()
        _api_keys = getattr(ctx, "api_keys", None) or getattr(
            request.app.state, "api_keys", {}
        )
        model_resolver = (
            resolve_vision_model_for_available_credentials
            if image_inputs
            else resolve_model_for_available_credentials
        )
        resolved_model = model_resolver(
            selected_model or getattr(request.app.state, "chat_model", ""),
            api_keys=_api_keys,
            fallback_models=(
                getattr(request.app.state, "chat_model", ""),
                *CHAT_MODEL_FALLBACKS,
            ),
        )
        resolved_provider = detect_provider(resolved_model)
        if not has_provider_api_key(resolved_provider, _api_keys):
            raise HTTPException(
                status_code=503,
                detail=(
                    "No LLM provider credentials are configured for chat. "
                    "Set GROQ_API_KEY or GROK_API_KEY, "
                    "OPENROUTER_API_KEY, "
                    "GEMINI_API_KEY or GEMINI_API_KEY, OPENAI_API_KEY, "
                    "or ANTHROPIC_API_KEY."
                ),
            )
        if image_inputs and not model_supports_vision(resolved_model):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Image uploads require a vision-capable chat model. "
                    "Configure GEMINI_API_KEY or GEMINI_API_KEY, OPENAI_API_KEY, "
                    "ANTHROPIC_API_KEY, or OPENROUTER_API_KEY."
                ),
            )
        if selected_model and resolved_model != selected_model:
            if image_inputs:
                logger.warning(
                    "Requested model %s is unavailable or lacks vision support for attachments; using %s instead",
                    selected_model,
                    resolved_model,
                )
            else:
                logger.warning(
                    "Requested model %s is unavailable with current credentials; using %s instead",
                    selected_model,
                    resolved_model,
                )

        allow_task_planning = _should_allow_task_planning(display_content)

        # Check if an existing task list exists for this thread.
        # If so, we nudge the model to continue it — UNLESS the user is explicitly
        # asking to create a new board (e.g. "make a task board"), in which case we
        # skip the "continue existing" hint.
        force_new_board = _should_force_task_planning(display_content)
        existing_task_board = None
        if allow_task_planning and not force_new_board:
            _store = request.app.state.task_tool.store_for(user.tenant_id)
            existing_task_board = await _store.get_by_conversation(str(body.thread_id))

        # Check if code interpreter runtime mounts the uploaded workspace bytes
        ci_has_workspace_access = bool(
            settings.SANDBOX_RUNTIME == "nsjail" or settings.CI_WORKSPACE_PVC_CLAIM
        )
        # Assemble declarative instruction blocks in priority order
        for block in (
            existing_task_board_block(bool(existing_task_board)),
            readonly_kb_block(ci_has_workspace_access),
            attachments_block(
                attachments, ci_has_workspace_access=ci_has_workspace_access
            ),
        ):
            deps["system_instructions"] += block

        if body.knowledge_base and ctx.knowledge is not None:
            deps["tools"] = _knowledge_tool_for(
                deps["tools"], ctx.knowledge, user.tenant_id, body.knowledge_base
            )
        if body.allowed_tools is not None:
            allowed = set(body.allowed_tools)
            deps["tools"] = [t for t in deps["tools"] if _tool_name(t) in allowed]
        if thread_agent is not None:
            # The agent's permissions are a ceiling a request can narrow further but never widen.
            deps["tools"] = narrow_tools(deps["tools"], thread_agent, _tool_name)
            deps["system_instructions"] += agent_instructions_block(
                thread_agent, drives
            )
        # Other agents of the user's it may ask for help (unless its permissions leave that out or the request narrowed it away). A conversation
        # that is itself a delegate never gets this: asking is one level deep.
        delegated = bool((thread.metadata_ or {}).get("delegated_from"))
        if (
            not delegated
            and (
                thread_agent is None
                or thread_agent.allowed_tools is None
                or TOOL_NAME in thread_agent.allowed_tools
            )
            and (body.allowed_tools is None or TOOL_NAME in body.allowed_tools)
        ):
            others = (
                await contacts_for(
                    ctx, user.tenant_id or "default", user.sub, thread_agent.id
                )
                if thread_agent
                else await other_agents(
                    ctx, user.tenant_id or "default", user.sub, None
                )
            )
            if others:
                deps["tools"] = [*deps["tools"], AskAgentTool(ctx, others, _tool_name)]

        if not allow_task_planning:
            deps["tools"] = [
                tool for tool in deps["tools"] if _tool_name(tool) != "manage_tasks"
            ]

        deps["tools"], deps["system_instructions"], initial_tool_choice = (
            _configure_workspace_mail_request(
                display_content,
                deps["tools"],
                deps["system_instructions"],
            )
        )
        if not initial_tool_choice:
            deps["tools"], deps["system_instructions"], initial_tool_choice = (
                _configure_calendar_write_request(
                    display_content,
                    deps["tools"],
                    deps["system_instructions"],
                )
            )
        # NOTE: manage_tasks is intentionally NOT force-injected here. The tool
        # stays available (when allow_task_planning) and the model decides on its
        # own whether a request warrants a task board — keyword-matching "plan"
        # and mandating a board produced spurious boards for simple questions.
        if initial_tool_choice:
            logger.info(
                "Thread %s: forcing first tool choice to %s via system prompt",
                body.thread_id,
                initial_tool_choice,
            )
        # Per-request model override — if the frontend sends a different model,
        # create a fresh client for this request only (supports any provider).
        if resolved_model:
            requested_provider = detect_provider(resolved_model)
            requested_bare_model = strip_provider_prefix(resolved_model)
            current_provider = getattr(deps["model_client"], "provider", None)
            current_model = getattr(deps["model_client"], "model", None)
            if (
                requested_provider != current_provider
                or requested_bare_model != current_model
            ):
                deps["model_client"] = create_model_client(
                    resolved_model,
                    api_keys=_api_keys,
                    **getattr(request.app.state, "model_client_kwargs", {}),
                )

        # The user's standing instructions and timezone come from their saved preferences, so they apply to every message from any
        # browser or device. An explicit ``system_instructions`` on the request (an API client) wins for that request.
        standing = (
            body.system_instructions
            if body.system_instructions is not None
            else instructions_for(await load_preferences(db, user))
        )
        deps["system_instructions"] += custom_instructions_block(standing)

        agent = await build_agent_for_thread(
            body.thread_id,
            model_client=deps["model_client"],
            tools=deps["tools"],
            system_instructions=deps["system_instructions"],
            cfg=settings,
            history=ctx.history,
            short_term_memory=ctx.short_term_memory,
            long_term_memory=ctx.long_term_memory,
            user_id=user.sub,
            tenant_id=user.tenant_id,
            model_context_window=settings.MODEL_CONTEXT_WINDOW,
            runtime=deps["runtime"],
            initial_tool_choice=initial_tool_choice or None,
            bridge=deps["bridge"],
            safety_middleware=ctx.safety_middleware,
            reasoning=ReasoningEffort(body.reasoning) if body.reasoning else None,
            # Virtual actor: a fresh, fully-current agent is rebuilt on every
            # turn anyway, so pinning this in the registry forever just leaks
            # one entry per thread ever chatted with. Evictable is safe here.
            pinned=False,
        )

        # 4. Extract user content from last message
        user_content = display_content
        if file_block:
            user_content = f"{file_block}\n\n---\n\n{user_content}"

        # Fire on_message hook
        hook_ctx = ChatContext(
            thread_id=body.thread_id,
            db=db,
            agent=agent,
        )
        await hooks.fire_message(hook_ctx, user_content)
        await db.commit()

    except Exception:
        raise

    bridge: WebHITLBridge = deps["bridge"]

    _user_blocks: list = build_user_blocks(user_content, image_inputs)
    _entry_msg = _Message(
        target=agent.id,
        sender=_Actor(type="http_proxy"),
        payload=_ChatPayload(
            message=_ChatMessage(role=Role.USER, content=_user_blocks)
        ),
        correlation_id=str(body.thread_id),
        metadata={
            "display_text": display_content,
            # Only what was newly attached THIS turn — see
            # _build_file_context's docstring for why this must not be the
            # broader `attachments` (model context) list.
            "attachments": new_attachments,
            "user_id": user.sub,
            "tenant_id": user.tenant_id,
            "branch_id": getattr(body, "branch_id", None) or "main",
            **(
                run_metadata(thread_agent.workspace_id, drives)
                if thread_agent is not None
                else {}
            ),
        },
    )

    _agent_spec = {
        "mode": "react",
        "agent_version": substrate.__version__,
        "system_instructions": deps["system_instructions"],
        "tool_names": [getattr(t, "name", "") for t in deps["tools"]],
        "max_iterations": 50 if initial_tool_choice else 30,
        "session_id": str(body.thread_id),
        "model_context_window": settings.MODEL_CONTEXT_WINDOW,
    }

    async def _settle_boards() -> list[dict]:
        """On clean run completion, settle this conversation's plan boards so
        lingering in-progress tasks stop spinning. Returns the updated board
        dicts for the session to push to the client."""
        store = request.app.state.task_tool.store_for(user.tenant_id)
        settled = await store.settle_conversation(str(body.thread_id))
        return [tl.to_dict() for tl in settled]

    session = AgentStreamSession(
        runtime=deps["runtime"],
        agent=agent,
        msg=_entry_msg,
        bridge=bridge,
        is_disconnected=request.is_disconnected,
        thread_id=str(body.thread_id),
        tenant_id=user.tenant_id,
        on_complete=_settle_boards,
        spec=_agent_spec,
    )

    async def sse_generator() -> AsyncIterator[str]:
        """Serialize the session's WireEvents as SSE `data:` lines.

        All concurrency (agent run, HITL merge, cancel/disconnect, persistence)
        lives in `AgentStreamSession`; this only frames events for the transport.
        Single-flight is enforced durably by Runtime.submit() itself (a unique
        index on run_queue), not by anything this generator owns, so
        there's no per-thread lock left to release here.
        """
        try:
            async for line in sse_lines(session, include_done=False):
                yield line
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("SSE generator error for thread %s", body.thread_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            await ctx.bridge_registry.release_if_idle(str(body.thread_id))
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        content=sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Protocol-Version": PROTOCOL_VERSION,
        },
    )


@router.get("/stream/{thread_id}", tags=["chat"])
async def stream_thread(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Reconnect to a thread's active run and relay its remaining wire events.

    For a browser that lost its original SSE connection (refresh, network
    drop) while the run kept executing durably server-side — NOT for
    starting a new run (use POST /chat for that). Read-only: this does not
    persist anything. The original request's AgentStreamSession already owns
    persistence for the run via its detached background task (see
    stream.session.AgentStreamSession.events()'s docstring) — that task
    keeps tailing and persisting independently of any UI connection until
    the run reaches a terminal state, regardless of whether anyone
    reconnects. A second tailer here calling persist_turn/persist_tool would
    save the same turns twice.

    A still-pending HITL card is NOT re-sent here — GET /hitl/status/{id}
    (called on page load, see substrate-ui's loadMessages) already restores that
    from the EventLogProtocol. This endpoint picks up from whatever's already known
    (``last_seq`` at connect time) onward, so the two are complementary, not
    duplicative.
    """
    runtime = ctx.runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")

    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    found = await runtime.active_run_for_thread(str(thread_id))

    async def _empty_generator() -> AsyncIterator[str]:
        yield "data: [DONE]\n\n"

    if found is None:
        # No active run for this thread — nothing to reconnect to (already
        # completed, or never started). Not an error: the frontend's own
        # message history load already has the final state in this case.
        return StreamingResponse(
            _empty_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    run_id = found.run_id
    from_seq = await runtime.store.last_seq(run_id) + 1

    async def sse_generator() -> AsyncIterator[str]:
        try:
            yield f"data: {json.dumps(HelloEvent().model_dump(mode='json'), default=str)}\n\n"
            async for wire in tail_wire_events(runtime, run_id, from_seq=from_seq):
                yield f"data: {json.dumps(wire.model_dump(mode='json'), default=str)}\n\n"
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Reconnect stream error for thread %s", thread_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Protocol-Version": PROTOCOL_VERSION,
        },
    )
