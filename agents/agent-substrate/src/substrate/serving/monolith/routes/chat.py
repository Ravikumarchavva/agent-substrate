"""Chat streaming endpoint with HITL support.

POST /chat – send a message, receive SSE stream of agent response
including tool approval requests, human input requests, and tool results.

Intent-routing heuristics, wire-event helpers, and per-request dependency
assembly live in ``chat_intents.py``, ``chat_wire.py``, and
``chat_context.py`` respectively — this module holds only the two route
handlers.
"""

from __future__ import annotations
from substrate.logger import setup_logging

import json
import substrate
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.shared.settings import settings
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
from substrate.infrastructure.serving_factory import build_agent_for_thread

# current_thread_id: ContextVar that scopes TaskManagerTool to the active thread.
# Defined in capabilities; both serving and capabilities need it — tracked as an
# explicit exception in the import-linter "serving boundary" contract.
from substrate.capabilities.tools.task_manager.tool import current_thread_id
from substrate.kernel.core.content import (
    ChatMessage as _ChatMessage,
    Role,
    TextBlock as _TextBlock,
)
from substrate.kernel.core.identity import AgentId as _AgentId
from substrate.kernel.messaging.message import (
    ChatPayload as _ChatPayload,
    Message as _Message,
)
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.hooks import ChatContext, hooks
from substrate.serving.monolith.schemas import ChatRequest
from substrate.serving.monolith.services import get_owned_thread
from substrate.serving.monolith.services.agent_service import persist_user_message
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.monolith.sse.bridge import WebHITLBridge
from substrate.serving.shared.rate_limit import rate_limit
from substrate.serving.protocol import PROTOCOL_VERSION, HelloEvent
from substrate.serving.stream import AgentStreamSession, tail_wire_events

from substrate.serving.monolith.routes.chat_intents import (
    ATTACHMENT_ANALYSIS_INSTRUCTIONS,
    _tool_name,
    _should_allow_task_planning,
    _should_force_task_planning,
    _configure_workspace_mail_request,
    _configure_calendar_write_request,
)
from substrate.serving.monolith.routes.chat_wire import (
    MediaType,
    _build_tool_meta_map,
    _WirePersister,
)
from substrate.serving.monolith.routes.chat_context import (
    _get_agent_deps,
    _build_file_context,
)

logger = setup_logging()

router = APIRouter(
    tags=["chat"],
    dependencies=[Depends(rate_limit), Depends(get_current_user)],
)


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
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

    # 2. Single-flight: only one active stream per thread at a time.
    # Durable and cross-replica: a unique partial index on ravi_run_queue
    # (see PostgresScheduler) is the actual enforcement, at Runtime.submit()
    # time — this is a cheap pre-check so the common case still gets a clean
    # 409 before the (comparatively expensive) agent build below runs, same
    # as the old per-process asyncio.Lock did. Unlike that lock, this holds
    # even when the existing run is being served by a different replica.
    # The rare race (two requests for the same thread both pass this check)
    # degrades gracefully instead of corrupting anything: submit() still
    # rejects one of them, surfaced as a run.failed SSE event since headers
    # are already sent by then — see AgentStreamSession._agent_worker.
    if await runtime.scheduler.find_run_for_thread(str(body.thread_id)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"A stream is already running for thread {body.thread_id}. "
                "Cancel it first via POST /chat/{thread_id}/cancel."
            ),
        )

    # 3. Build agent with restored memory + per-thread HITL bridge
    try:
        deps = await _get_agent_deps(ctx, str(body.thread_id))
        file_block, image_inputs, attachments = await _build_file_context(
            db,
            body,
            request,
            ctx,
        )
        if not body.messages:
            raise HTTPException(status_code=422, detail="messages[] must not be empty")
        display_content = body.messages[-1].content

        selected_model = (
            body.model or getattr(request.app.state, "chat_model", "")
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
        if allow_task_planning and not force_new_board:
            _store = request.app.state.task_tool.store
            _existing = await _store.get_by_conversation(str(body.thread_id))
            if _existing:
                deps["system_instructions"] = (
                    deps["system_instructions"]
                    + "\n\n---\n**Existing task board:**\n"
                    + "A Kanban task list already exists for this conversation. "
                    + "If the user is providing new details, context, or corrections, "
                    + "call manage_tasks action=create_list again with updated, more specific tasks "
                    + "that incorporate their new information. "
                    + "Otherwise continue working through the existing tasks."
                )

        if attachments:
            deps["system_instructions"] = (
                deps["system_instructions"]
                + "\n\n---\n**Attachment handling instructions:**\n"
                + ATTACHMENT_ANALYSIS_INSTRUCTIONS
            )

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

        # Append per-request custom instructions if provided by the frontend
        if body.system_instructions and body.system_instructions.strip():
            deps["system_instructions"] = (
                deps["system_instructions"]
                + "\n\n---\n**Additional instructions from user:**\n"
                + body.system_instructions.strip()
            )

        agent = await build_agent_for_thread(
            db,
            body.thread_id,
            model_client=deps["model_client"],
            tools=deps["tools"],
            system_instructions=deps["system_instructions"],
            history=ctx.history,
            model_context_window=settings.MODEL_CONTEXT_WINDOW,
            runtime=deps["runtime"],
            initial_tool_choice=initial_tool_choice or None,
        )

        # 4. Extract user content from last message
        user_content = display_content
        if file_block:
            user_content = f"{file_block}\n\n---\n\n{user_content}"
        user_input_content: list[MediaType] | None = None
        if image_inputs:
            user_input_content = []
            if user_content:
                user_input_content.append(user_content)
            user_input_content.extend(image_inputs)

        # Fire on_message hook
        hook_ctx = ChatContext(
            thread_id=body.thread_id,
            db=db,
            agent=agent,
        )
        await hooks.fire_message(hook_ctx, user_content)

        # Persist user message
        user_metadata = (
            {
                "display_content": display_content,
                "attachments": attachments,
            }
            if attachments
            else None
        )
        await persist_user_message(
            db,
            body.thread_id,
            user_content,
            metadata=user_metadata,
        )
        await db.commit()

    except Exception:
        # No per-thread lock to release anymore (single-flight is enforced
        # durably by Runtime.submit()'s unique-index check, not a lock this
        # handler owns) — this except exists only to preserve the original
        # exception's traceback/type on the way out.
        raise
    # Per-thread HITL bridge (acquired in _get_agent_deps).
    bridge: WebHITLBridge = deps["bridge"]

    tool_meta_map = _build_tool_meta_map(deps["tools"])

    # current_thread_id is set inside sse_generator (with reset) to scope it
    # to the streaming task and avoid leaking into the request handler scope.

    persister = _WirePersister(
        session_factory=ctx.session_factory,
        thread_id=body.thread_id,
        tool_meta_map=tool_meta_map,
        attachments=attachments,
    )

    _user_blocks: list = [_TextBlock(text=user_content)]
    _entry_msg = _Message(
        target=agent.id,
        sender=_AgentId(type="proxy", key="http"),
        payload=_ChatPayload(
            message=_ChatMessage(role=Role.USER, content=_user_blocks)
        ),
        correlation_id=str(body.thread_id),
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
        store = request.app.state.task_tool.store
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
        persister=persister,
        on_complete=_settle_boards,
        spec=_agent_spec,
    )

    async def sse_generator() -> AsyncIterator[str]:
        """Serialize the session's WireEvents as SSE `data:` lines.

        All concurrency (agent run, HITL merge, cancel/disconnect, persistence)
        lives in `AgentStreamSession`; this only frames events for the transport.
        Single-flight is enforced durably by Runtime.submit() itself (a unique
        index on ravi_run_queue), not by anything this generator owns, so
        there's no per-thread lock left to release here.
        """
        _thread_id_token = current_thread_id.set(str(body.thread_id))
        try:
            async for event in session.events():
                yield f"data: {json.dumps(event.model_dump(mode='json'), default=str)}\n\n"
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("SSE generator error for thread %s", body.thread_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            current_thread_id.reset(_thread_id_token)
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
    db: AsyncSession = Depends(get_db),
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
    (called on page load, see ravi-ui's loadMessages) already restores that
    from the EventLog. This endpoint picks up from whatever's already known
    (``last_seq`` at connect time) onward, so the two are complementary, not
    duplicative.
    """
    runtime = ctx.runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")

    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    found = await runtime.scheduler.find_run_for_thread(str(thread_id))

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

    run_id, _status = found
    event_log = runtime.event_log
    from_seq = await event_log.last_seq(run_id) + 1

    async def sse_generator() -> AsyncIterator[str]:
        _thread_id_token = current_thread_id.set(str(thread_id))
        try:
            yield f"data: {json.dumps(HelloEvent().model_dump(mode='json'), default=str)}\n\n"
            async for wire in tail_wire_events(event_log, run_id, from_seq=from_seq):
                yield f"data: {json.dumps(wire.model_dump(mode='json'), default=str)}\n\n"
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Reconnect stream error for thread %s", thread_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            current_thread_id.reset(_thread_id_token)
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
