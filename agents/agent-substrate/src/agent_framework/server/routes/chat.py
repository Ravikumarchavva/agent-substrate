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
from agent_framework.core.messages import CompletionChunk, ReasoningDeltaChunk, TextDeltaChunk
from agent_framework.core.messages.client_messages import AssistantMessage, ToolExecutionResultMessage
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
    get_files_by_ids,
    to_vision_image_block,
)
from agent_framework.server.routes.mcp_apps import resolve_ui_uri
from agent_framework.extensions.tools.task_manager_tool import current_thread_id
from agent_framework.extensions.tools.web_surfer import WebSurferTool
from agent_framework.runtime.hitl import WebHITLBridge, _DONE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _get_agent_deps(request: Request):
    """Extract shared agent dependencies from app state, adding WebSurferTool."""
    tools = list(request.app.state.tools)
    # Only add if not already present
    if not any(isinstance(t, WebSurferTool) for t in tools):
        tools.append(WebSurferTool())
    return {
        "model_client": request.app.state.model_client,
        "tools": tools,
        "system_instructions": request.app.state.system_instructions,
        "tool_approval_handler": getattr(request.app.state, "tool_approval_handler", None),
        "tools_requiring_approval": getattr(request.app.state, "tools_requiring_approval", None),
        "tool_timeout": getattr(request.app.state, "tool_timeout", None),
    }


def _build_tool_meta_map(tools: list) -> dict:
    """Build a mapping of tool_name → _meta dict for tools that have UI metadata."""
    meta_map: dict = {}
    for tool in tools:
        try:
            schema = tool.get_schema()
            if schema.meta and schema.meta.get("ui"):
                meta_map[schema.name] = schema.meta
        except Exception as e:
            logger.warning(f"Failed to get schema for tool {getattr(tool, 'name', 'unknown')}: {e}")
    return meta_map


async def _build_file_context(
    db: AsyncSession,
    body: ChatRequest,
    request: Request,
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

    elements = await get_files_by_ids(db, body.file_ids, body.thread_id)
    if not elements:
        return "", []

    text_parts: list[str] = []
    image_notes: list[str] = []

    for el in elements:
        extracted = extract_text(el)
        if extracted is not None:
            text_parts.append(
                f"### File: {el.name}\n"
                f"```\n{extracted}\n```"
            )
        elif (el.mime or "").startswith("image/"):
            image_notes.append(
                f"[Image attached: {el.name} — "
                f"available at /data/{el.name} in the code interpreter]"
            )
        else:
            # Unknown binary — just note it exists in the CI VM
            text_parts.append(
                f"### File: {el.name} ({el.mime or 'binary'})\n"
                f"(Binary file — available at /data/{el.name} in the code interpreter)"
            )

    # Push every file to the code-interpreter VM so the agent can
    # use pandas, PIL, etc. to work with them programmatically.
    ci_client = getattr(request.app.state, "ci_client", None)
    if ci_client:
        import base64 as _b64
        session_id = str(body.thread_id)
        for el in elements:
            if not el.content:
                continue
            try:
                b64 = _b64.b64encode(el.content).decode()
                await ci_client.write_file(
                    session_id,
                    path=f"/data/{el.name}",
                    content=b64,
                    encoding="base64",
                )
                logger.info("Pushed file %s (%d bytes) to CI session %s",
                            el.name, len(el.content), session_id)
            except Exception as exc:
                logger.warning("Failed to push %s to CI VM: %s", el.name, exc)

    if not text_parts and not image_notes:
        return "", image_notes

    names = ", ".join(el.name for el in elements)
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
    db: AsyncSession = Depends(get_db),
):
    """Stream agent response as Server-Sent Events with HITL support.

    Flow:
      1. Validate thread exists
      2. Rebuild agent memory from DB
      3. Fire on_message hook
      4. Persist user message to DB
      5. Stream agent response (yielding SSE events) via merged queue
         - agent chunks (text_delta, reasoning_delta, completion)
         - HITL events (tool_approval_request, human_input_request)
         - tool results
      6. Persist assistant response + tool results to DB
    """
    # 1. Validate thread
    thread = await get_thread(db, body.thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # 2. Build agent with restored memory + HITL support
    deps = _get_agent_deps(request)

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
        tool_approval_handler=deps["tool_approval_handler"],
        tools_requiring_approval=deps["tools_requiring_approval"],
        tool_timeout=deps["tool_timeout"],
    )

    # 3. Extract user content from last message
    user_content = body.messages[-1].content

    # 3a. Inject attached file context (text extraction + CI VM push)
    file_block, image_notes = await _build_file_context(db, body, request)
    if file_block:
        user_content = f"{file_block}\n\n---\n\n{user_content}"
    if image_notes:
        user_content = "\n".join(image_notes) + "\n\n" + user_content

    # 4. Fire on_message hook
    ctx = ChatContext(
        thread_id=body.thread_id,
        db=db,
        agent=agent,
    )
    await hooks.fire_message(ctx, user_content)

    # 5. Persist user message
    await persist_user_message(db, body.thread_id, user_content)
    await db.commit()

    # Get the HITL bridge from app state
    bridge: WebHITLBridge = request.app.state.bridge

    async def sse_generator() -> AsyncIterator[str]:
        """Yield SSE events from merged agent + HITL streams, persist results."""
        final_message: AssistantMessage | None = None

        # Build tool _meta lookup for MCP Apps UI metadata
        tool_meta_map = _build_tool_meta_map(deps["tools"])

        # Merged queue: both agent chunks and HITL events flow through here
        merged_queue: asyncio.Queue = asyncio.Queue()

        # Per-request cancel signal — set by POST /chat/{thread_id}/cancel
        cancel_event: asyncio.Event = asyncio.Event()
        request.app.state.cancel_registry[body.thread_id] = cancel_event

        async def agent_worker():
            """Run the agent stream and push chunks to the merged queue."""
            try:
                async for chunk in agent.run_stream(user_content):
                    await merged_queue.put(("agent", chunk))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                merged_queue.put_nowait(("error", str(e)))
            finally:
                # Use put_nowait (non-blocking) so this is safe inside
                # a finally block that runs during task cancellation.
                merged_queue.put_nowait(("agent_done", None))

        async def hitl_worker():
            """Drain HITL events from the bridge and push to the merged queue."""
            while True:
                event = await bridge.get_event()
                if event is _DONE:
                    break
                await merged_queue.put(("hitl", event))

        # Bind the task‑manager ContextVar so TaskManagerTool knows which
        # conversation it belongs to (safe for concurrent requests).
        current_thread_id.set(body.thread_id)

        # Start both workers
        agent_task = asyncio.create_task(agent_worker())
        hitl_task = asyncio.create_task(hitl_worker())

        try:
            bridge_signaled = False
            while True:
                # Poll with a short timeout so we can detect cancellation
                # even while waiting for the next queue item.
                try:
                    source, data = await asyncio.wait_for(
                        merged_queue.get(), timeout=0.2
                    )
                except asyncio.TimeoutError:
                    if cancel_event.is_set():
                        logger.info(
                            "Cancellation detected for thread %s", body.thread_id
                        )
                        # Stop the agent task
                        if not agent_task.done():
                            agent_task.cancel()
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(agent_task), timeout=3.0
                                )
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                        # Signal HITL bridge to stop
                        if not bridge_signaled:
                            bridge_signaled = True
                            await bridge.signal_done()
                        yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                        break
                    continue

                if source == "agent":
                    chunk = data
                    try:
                        if isinstance(chunk, TextDeltaChunk):
                            payload = {
                                "type": "text_delta",
                                "content": chunk.text,
                                "partial": True,
                            }
                            yield f"data: {json.dumps(payload)}\n\n"

                        elif isinstance(chunk, ReasoningDeltaChunk):
                            payload = {
                                "type": "reasoning_delta",
                                "content": chunk.text,
                                "partial": True,
                            }
                            yield f"data: {json.dumps(payload)}\n\n"

                        elif isinstance(chunk, CompletionChunk):
                            message = chunk.message
                            final_message = message

                            # Serialize tool calls, enriching with _meta UI info
                            serialized_tool_calls = None
                            has_tool_calls = bool(message.tool_calls)
                            if message.tool_calls:
                                serialized_tool_calls = []
                                for tc in message.tool_calls:
                                    tc_data: dict = {
                                        "id": tc.id,
                                        "name": tc.name,
                                        "arguments": tc.arguments,
                                    }
                                    # Attach _meta.ui if this tool has an MCP App
                                    meta = tool_meta_map.get(tc.name)
                                    if meta:
                                        ui_info = meta.get("ui", {})
                                        resource_uri = ui_info.get("resourceUri", "")
                                        http_url = resolve_ui_uri(resource_uri) if resource_uri else None
                                        tc_data["_meta"] = {
                                            "ui": {
                                                "resourceUri": resource_uri,
                                                "httpUrl": http_url or resource_uri,
                                            }
                                        }
                                    serialized_tool_calls.append(tc_data)

                            payload = {
                                "type": "completion",
                                "role": message.role,
                                "content": message.content,
                                "tool_calls": serialized_tool_calls,
                                "finish_reason": message.finish_reason,
                                "has_tool_calls": has_tool_calls,
                                "usage": {
                                    "prompt_tokens": message.usage.prompt_tokens,
                                    "completion_tokens": message.usage.completion_tokens,
                                    "total_tokens": message.usage.total_tokens,
                                }
                                if message.usage
                                else None,
                                "partial": False,
                                "complete": True,
                            }
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

                            # Persist EVERY assistant message immediately
                            # (intermediate ones with tool_calls AND the final text one)
                            # This ensures the conversation history stays valid for
                            # multi-turn sessions — tool_results need their parent
                            # assistant message with matching call_ids in memory.
                            try:
                                async with request.app.state.session_factory() as persist_db:
                                    await persist_assistant_message(
                                        persist_db,
                                        body.thread_id,
                                        message,
                                        tool_meta_map=tool_meta_map,
                                    )
                                    await persist_db.commit()
                            except Exception:
                                logger.exception("Failed to persist assistant message")

                        elif isinstance(chunk, ToolExecutionResultMessage):
                            # Tool result — send to frontend + persist
                            content_text = ""
                            if isinstance(chunk.content, list):
                                parts = []
                                for block in chunk.content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        parts.append(block.get("text", ""))
                                content_text = "\n".join(parts)

                            tool_name = getattr(chunk, "name", "unknown")
                            tool_http_url = ""
                            if tool_name in tool_meta_map:
                                meta_info = tool_meta_map[tool_name]
                                ui_info = meta_info.get("ui", {})
                                resource_uri = ui_info.get("resourceUri", "")
                                tool_http_url = resolve_ui_uri(resource_uri) if resource_uri else f"/ui/{tool_name}"
                            payload = {
                                "type": "tool_result",
                                "tool_name": tool_name,
                                "tool_call_id": getattr(chunk, "tool_call_id", ""),
                                "content": content_text,
                                "is_error": getattr(chunk, "isError", False),
                                "has_app": tool_name in tool_meta_map,
                                "http_url": tool_http_url,
                                "app_data": getattr(chunk, "app_data", None),
                                "partial": False,
                            }
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

                            # Persist tool result to DB
                            try:
                                async with request.app.state.session_factory() as persist_db:
                                    await persist_tool_result(
                                        persist_db,
                                        body.thread_id,
                                        tool_call_id=getattr(chunk, "tool_call_id", ""),
                                        tool_name=getattr(chunk, "name", "unknown"),
                                        output=content_text,
                                        is_error=getattr(chunk, "isError", False),
                                    )
                                    await persist_db.commit()
                            except Exception:
                                logger.exception("Failed to persist tool result")

                        else:
                            payload = {
                                "type": "unknown",
                                "content": str(chunk),
                                "partial": True,
                            }
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, default=str)}\n\n"

                elif source == "hitl":
                    # HITL event — forward directly to frontend
                    yield f"data: {json.dumps(data, default=str)}\n\n"

                elif source == "error":
                    yield f"data: {json.dumps({'type': 'error', 'error': data}, default=str)}\n\n"

                elif source == "agent_done":
                    # Signal the HITL worker to stop
                    if not bridge_signaled:
                        bridge_signaled = True
                        await bridge.signal_done()
                    break

        except Exception as e:
            logger.exception("Error in SSE generator")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        finally:
            # Clean up cancel registry for this thread
            request.app.state.cancel_registry.pop(body.thread_id, None)
            # Ensure tasks are cleaned up
            if not agent_task.done():
                agent_task.cancel()
            if not hitl_task.done():
                hitl_task.cancel()

        # All assistant messages are now persisted inline above,
        # no deferred persistence needed.

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        content=sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
