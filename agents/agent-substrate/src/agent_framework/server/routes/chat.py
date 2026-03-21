"""Chat streaming endpoint with HITL support.

POST /chat – send a message, receive SSE stream of agent response
including tool approval requests, human input requests, and tool results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.core.agents.react_agent import ReActAgent
from agent_framework.configs.settings import settings
from agent_framework.core.messages import CompletionChunk, ReasoningDeltaChunk, TextDeltaChunk
from agent_framework.core.messages.client_messages import AssistantMessage, ToolExecutionResultMessage
from agent_framework.server.context import ServerContext, get_ctx
from agent_framework.server.database import get_db
from agent_framework.server.hooks import ChatContext, hooks
from agent_framework.server.schemas import ChatRequest
from agent_framework.server.services import get_thread
from agent_framework.server.services.agent_service import (
    load_agent_for_thread,
    persist_assistant_message,
    persist_tool_result,
    persist_user_message,
)
from agent_framework.server.services.file_service import (
    extract_text,
    get_file_content,
    get_files_by_ids,
    to_vision_image_block,
)
from agent_framework.server.routes.mcp_apps import resolve_ui_uri
from agent_framework.extensions.tools.task_manager_tool import current_thread_id
from agent_framework.extensions.tools.file_manager_tool import current_thread_id as file_thread_id
from agent_framework.extensions.tools.web_surfer import WebSurferTool
from agent_framework.extensions.tools.human_input import AskHumanTool
from agent_framework.runtime.hitl import BRIDGE_DONE, BridgeRegistry, WebHITLBridge
from agent_framework.runtime.events import (
    EventBus,
    BUS_CLOSED,
    TextDeltaEvent,
    ReasoningDeltaEvent,
    ErrorEvent,
    RawDictEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


async def _get_agent_deps(ctx: ServerContext, thread_id: str):
    """Assemble per-request agent dependencies with an isolated HITL bridge."""
    bridge_registry: BridgeRegistry = ctx.bridge_registry
    bridge = await bridge_registry.acquire(str(thread_id))

    # Build a fresh AskHumanTool for this request wired to the thread's bridge.
    # Removes the placeholder from ctx.tools so only one instance exists.
    base_tools = [
        t for t in ctx.tools
        if not isinstance(t, AskHumanTool)
    ]
    ask_tool = AskHumanTool(
        handler=bridge.human_handler,
        max_requests_per_run=5,
    )
    tools = [ask_tool] + base_tools

    # Only add WebSurferTool if not already present
    if not any(isinstance(t, WebSurferTool) for t in tools):
        tools.append(WebSurferTool())

    return {
        "model_client": ctx.model_client,
        "tools": tools,
        "system_instructions": ctx.system_instructions,
        "tool_approval_handler": bridge.approval_handler,
        "tools_requiring_approval": ctx.tools_requiring_approval,
        "tool_timeout": ctx.tool_timeout,
        "bridge": bridge,
    }


def _build_tool_meta_map(tools: list) -> dict:
    """Build a mapping of tool_name → { risk, color, ui? } for event enrichment."""
    meta_map: dict = {}
    for tool in tools:
        try:
            schema = tool.get_schema()
            entry: dict = {
                "risk": schema.risk,
                "color": getattr(tool, "risk", None) and tool.risk.color or "green",
            }
            if schema.meta and schema.meta.get("ui"):
                entry["ui"] = schema.meta["ui"]
            meta_map[schema.name] = entry
        except Exception as e:
            logger.warning("Failed to get schema for tool %s: %s", getattr(tool, "name", "unknown"), e)
    return meta_map


def _build_completion_payload(message: AssistantMessage, tool_meta_map: dict) -> dict:
    """Build the SSE ``completion`` event payload from an ``AssistantMessage``.

    Extracts tool calls, decorates them with risk/colour/MCP-App metadata,
    and assembles the full dict sent over the wire.
    """
    serialized_tool_calls = None
    if message.tool_calls:
        serialized_tool_calls = []
        for tc in message.tool_calls:
            tc_data: dict = {
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            }
            meta = tool_meta_map.get(tc.name)
            if meta:
                tc_data["risk"]  = meta.get("risk", "safe")
                tc_data["color"] = meta.get("color", "green")
                ui_info = meta.get("ui")
                if ui_info:
                    resource_uri = ui_info.get("resourceUri", "")
                    http_url = resolve_ui_uri(resource_uri) if resource_uri else None
                    tc_data["_meta"] = {
                        "ui": {
                            "resourceUri": resource_uri,
                            "httpUrl": http_url or resource_uri,
                        }
                    }
            serialized_tool_calls.append(tc_data)

    return {
        "type": "completion",
        "role": message.role,
        "content": message.content,
        "tool_calls": serialized_tool_calls,
        "finish_reason": message.finish_reason,
        "has_tool_calls": bool(message.tool_calls),
        "usage": {
            "prompt_tokens": message.usage.prompt_tokens,
            "completion_tokens": message.usage.completion_tokens,
            "total_tokens": message.usage.total_tokens,
        } if message.usage else None,
        "partial": False,
        "complete": True,
    }


def _build_tool_result_payload(chunk: ToolExecutionResultMessage, tool_meta_map: dict) -> dict:
    """Build the SSE ``tool_result`` event payload from a ``ToolExecutionResultMessage``."""
    content_text = ""
    if isinstance(chunk.content, list):
        parts = []
        for block in chunk.content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        content_text = "\n".join(parts)

    tool_name = getattr(chunk, "name", "unknown")
    tool_meta = tool_meta_map.get(tool_name, {})
    tool_http_url = ""
    if "ui" in tool_meta:
        ui_info = tool_meta["ui"]
        resource_uri = ui_info.get("resourceUri", "")
        tool_http_url = resolve_ui_uri(resource_uri) if resource_uri else f"/ui/{tool_name}"

    return {
        "type": "tool_result",
        "tool_name": tool_name,
        "tool_call_id": getattr(chunk, "tool_call_id", ""),
        "content": content_text,
        "is_error": chunk.is_error,
        "has_app": "ui" in tool_meta,
        "http_url": tool_http_url,
        "app_data": getattr(chunk, "app_data", None),
        "risk":  tool_meta.get("risk", "safe"),
        "color": tool_meta.get("color", "green"),
        "partial": False,
        # Carry raw content text for persistence — not sent to frontend
        "_raw_content": content_text,
    }


async def _build_file_context(
    db: AsyncSession,
    body: ChatRequest,
    request: Request,
    ctx: ServerContext,
) -> tuple[str, list[str]]:
    """Load file IDs from the request, extract text and push to CI VM.

    Returns:
        (file_context_block, image_descriptions) where
        - file_context_block is a formatted string to prepend to the user
          message (empty string when no files were requested), and
        - image_descriptions is a list of human-readable image annotations
          (e.g. '[Image: photo.png is available at /data/photo.png]').
    """
    if not body.file_ids:
        return "", []

    store = ctx.file_store
    files = await get_files_by_ids(db, body.file_ids, body.thread_id)
    if not files:
        return "", []

    text_parts: list[str] = []
    image_notes: list[str] = []

    for meta in files:
        extracted = await extract_text(store, meta)
        if extracted is not None:
            text_parts.append(
                f"### File: {meta.original_name}\n"
                f"```\n{extracted}\n```"
            )
        elif (meta.content_type or "").startswith("image/"):
            image_notes.append(
                f"[Image attached: {meta.original_name} — "
                f"available at /data/{meta.original_name} in the code interpreter]"
            )
        else:
            # Unknown binary — just note it exists in the CI VM
            text_parts.append(
                f"### File: {meta.original_name} ({meta.content_type or 'binary'})\n"
                f"(Binary file — available at /data/{meta.original_name} in the code interpreter)"
            )

    # Push every file to the code-interpreter VM so the agent can
    # use pandas, PIL, etc. to work with them programmatically.
    ci_client = ctx.ci_client
    if ci_client:
        import base64 as _b64
        session_id = str(body.thread_id)
        for meta in files:
            try:
                raw = await get_file_content(store, meta)
                b64 = _b64.b64encode(raw).decode()
                await ci_client.write_file(
                    session_id,
                    path=f"/data/{meta.original_name}",
                    content=b64,
                    encoding="base64",
                )
                logger.info("Pushed file %s (%d bytes) to CI session %s",
                            meta.original_name, meta.size_bytes, session_id)
            except Exception as exc:
                logger.warning("Failed to push %s to CI VM: %s", meta.original_name, exc)

    if not text_parts and not image_notes:
        return "", image_notes

    names = ", ".join(m.original_name for m in files)
    sections = "\n\n".join(text_parts)
    block = (
        f"The user has attached {len(elements)} file(s): {names}.\n"
        f"File contents:\n\n{sections}"
    )
    return block, image_notes


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    ctx: ServerContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    """Stream agent response as Server-Sent Events with HITL support.

    Flow:
      1. Validate thread exists
      2. Single-flight check — 409 if same thread already has a running stream
      3. Build agent with restored memory + per-thread HITL bridge
      4. Fire on_message hook, persist user message
      5. Stream response via EventBus (typed events: text_delta, completion,
         tool_result, HITL events, error)
      6. Persist assistant messages and tool results inline as they arrive
    """
    # 1. Validate thread
    thread = await get_thread(db, body.thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # 2. Single-flight: only one active stream per thread at a time
    thread_lock = ctx.thread_locks.setdefault(str(body.thread_id), asyncio.Lock())
    if thread_lock.locked():
        raise HTTPException(
            status_code=409,
            detail=(
                f"A stream is already running for thread {body.thread_id}. "
                "Cancel it first via POST /chat/{thread_id}/cancel."
            ),
        )
    await thread_lock.acquire()

    # 3. Build agent with restored memory + per-thread HITL bridge
    # Guard: release the lock if any pre-stream setup step throws so the lock
    # is never orphaned (sse_generator's finally only runs once iterated).
    try:
        deps = await _get_agent_deps(ctx, body.thread_id)

        # Append per-request custom instructions if provided by the frontend
        if body.system_instructions and body.system_instructions.strip():
            deps["system_instructions"] = (
                deps["system_instructions"]
                + "\n\n---\n**Additional instructions from user:**\n"
                + body.system_instructions.strip()
            )

        agent = await load_agent_for_thread(
            db,
            body.thread_id,
            model_client=deps["model_client"],
            tools=deps["tools"],
            system_instructions=deps["system_instructions"],
            redis_memory=ctx.redis_memory,
            model_context_window=settings.MODEL_CONTEXT_WINDOW,
            tool_approval_handler=deps["tool_approval_handler"],
            tools_requiring_approval=deps["tools_requiring_approval"],
            tool_timeout=deps["tool_timeout"],
        )

        # 4. Extract user content from last message
        if not body.messages:
            raise HTTPException(status_code=422, detail="messages[] must not be empty")
        user_content = body.messages[-1].content

        # 4a. Inject attached file context (text extraction + CI VM push)
        file_block, image_notes = await _build_file_context(db, body, request, ctx)
        if file_block:
            user_content = f"{file_block}\n\n---\n\n{user_content}"
        if image_notes:
            user_content = "\n".join(image_notes) + "\n\n" + user_content

        # Fire on_message hook
        hook_ctx = ChatContext(
            thread_id=body.thread_id,
            db=db,
            agent=agent,
        )
        await hooks.fire_message(hook_ctx, user_content)

        # Persist user message
        await persist_user_message(db, body.thread_id, user_content)
        await db.commit()

    except Exception:
        thread_lock.release()
        ctx.thread_locks.pop(str(body.thread_id), None)
        raise

    # Per-thread HITL bridge (acquired in _get_agent_deps)
    bridge: WebHITLBridge = deps["bridge"]

    async def sse_generator() -> AsyncIterator[str]:
        """Yield SSE events via ``EventBus`` from merged agent + HITL workers.

        Architecture:
          - ``agent_worker`` runs ``agent.run_stream()``, emits typed events, and
            persists completion/tool-result messages to Postgres inline before
            emitting so the DB is always consistent with the SSE output.
          - ``hitl_worker`` drains HITL events from the bridge and emits them as
            ``RawDictEvent`` entries; calls ``bus.close()`` when all events are
            consumed (after agent signals bridge done).
          - The consumer loop polls the bus with a 200 ms timeout so it can
            detect browser disconnect or explicit cancel between events.
        """
        tool_meta_map = _build_tool_meta_map(deps["tools"])
        bus: EventBus = EventBus()
        bridge_signaled = False

        # Per-request cancel signal — set by POST /chat/{thread_id}/cancel
        # Key MUST be str to match cancel.py which receives thread_id as a path param.
        cancel_event: asyncio.Event = asyncio.Event()
        ctx.cancel_registry[str(body.thread_id)] = cancel_event

        async def agent_worker() -> None:
            """Run agent; emit typed events and persist inline to Postgres."""
            nonlocal bridge_signaled
            try:
                async for chunk in agent.run_stream(user_content):
                    if isinstance(chunk, TextDeltaChunk):
                        await bus.emit(TextDeltaEvent(content=chunk.text, partial=True))

                    elif isinstance(chunk, ReasoningDeltaChunk):
                        await bus.emit(ReasoningDeltaEvent(content=chunk.text, partial=True))

                    elif isinstance(chunk, CompletionChunk):
                        payload = _build_completion_payload(chunk.message, tool_meta_map)
                        # Persist BEFORE emitting so Postgres and SSE stay in sync
                        try:
                            async with ctx.session_factory() as persist_db:
                                await persist_assistant_message(
                                    persist_db,
                                    body.thread_id,
                                    chunk.message,
                                    tool_meta_map=tool_meta_map,
                                )
                                await persist_db.commit()
                        except Exception:
                            logger.exception("Failed to persist assistant message")
                        await bus.emit_dict(payload)

                    elif isinstance(chunk, ToolExecutionResultMessage):
                        payload = _build_tool_result_payload(chunk, tool_meta_map)
                        raw_content = payload.pop("_raw_content", "")
                        # Persist BEFORE emitting
                        try:
                            async with ctx.session_factory() as persist_db:
                                await persist_tool_result(
                                    persist_db,
                                    body.thread_id,
                                    tool_call_id=getattr(chunk, "tool_call_id", ""),
                                    tool_name=getattr(chunk, "name", "unknown"),
                                    output=raw_content,
                                    is_error=chunk.is_error,
                                )
                                await persist_db.commit()
                        except Exception:
                            logger.exception("Failed to persist tool result")
                        await bus.emit_dict(payload)

                    else:
                        await bus.emit_dict(
                            {"type": "unknown", "content": str(chunk), "partial": True}
                        )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await bus.emit(ErrorEvent(message=str(exc)))
            finally:
                # Signal HITL worker to stop; it will close the bus after draining
                if not bridge_signaled:
                    bridge_signaled = True
                    await bridge.signal_done()

        async def hitl_worker() -> None:
            """Forward HITL events to bus; close bus when the agent is done."""
            while True:
                event = await bridge.get_event()
                if event is BRIDGE_DONE:
                    break
                await bus.emit_dict(event)
            # All HITL events flushed — signal consumer to stop
            bus.close()

        # Bind ContextVars so tools route events to this thread
        current_thread_id.set(body.thread_id)
        file_thread_id.set(str(body.thread_id))

        agent_task = asyncio.create_task(agent_worker())
        hitl_task = asyncio.create_task(hitl_worker())

        exit_reason = "disconnect"

        try:
            while True:
                # Timeout-based poll so we can detect disconnect/cancel between events
                try:
                    item = await bus.poll(0.2)
                except asyncio.TimeoutError:
                    # ── Disconnect detection ─────────────────────────────────
                    if await request.is_disconnected():
                        logger.info(
                            "Client disconnected for thread %s", body.thread_id
                        )
                        resolved = bridge.cancel_all_pending("session_disconnected")
                        if resolved:
                            logger.info(
                                "Thread %s: resolved %d pending HITL request(s) "
                                "with session_disconnected",
                                body.thread_id, resolved,
                            )
                        if not agent_task.done():
                            agent_task.cancel()
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(agent_task), timeout=3.0
                                )
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                        if not bridge_signaled:
                            bridge_signaled = True
                            await bridge.signal_done()
                        exit_reason = "disconnect"
                        break

                    # ── Explicit cancel ──────────────────────────────────────
                    if cancel_event.is_set():
                        logger.info(
                            "Cancellation detected for thread %s", body.thread_id
                        )
                        if not agent_task.done():
                            agent_task.cancel()
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(agent_task), timeout=3.0
                                )
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                        if not bridge_signaled:
                            bridge_signaled = True
                            await bridge.signal_done()
                        yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                        exit_reason = "cancelled"
                        break
                    continue

                # ── Bus closed = both workers finished normally ───────────────
                if item is BUS_CLOSED:
                    exit_reason = "completed"
                    break

                # ── Dispatch event to SSE transport ──────────────────────────
                if isinstance(item, TextDeltaEvent):
                    yield bus.to_sse_line(item)

                elif isinstance(item, ReasoningDeltaEvent):
                    yield bus.to_sse_line(item)

                elif isinstance(item, ErrorEvent):
                    yield bus.to_sse_line(item)

                elif isinstance(item, RawDictEvent):
                    yield f"data: {json.dumps(item.data, default=str)}\n\n"

                else:
                    try:
                        yield f"data: {json.dumps(item.to_dict(), default=str)}\n\n"
                    except Exception:
                        yield (
                            f"data: {json.dumps({'type': 'unknown', 'content': str(item)})}\n\n"
                        )

        except Exception as exc:
            logger.exception("SSE generator error for thread %s", body.thread_id)
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        finally:
            ctx.cancel_registry.pop(str(body.thread_id), None)
            thread_lock.release()
            ctx.thread_locks.pop(str(body.thread_id), None)

            # Cancel and await both worker tasks to prevent orphaned coroutines.
            for task in (agent_task, hitl_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(agent_task, hitl_task, return_exceptions=True)

            await ctx.bridge_registry.release_if_idle(str(body.thread_id))

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        content=sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
