"""WebSocket chat endpoint — bidirectional, full-duplex replacement for SSE.

Why WebSocket over SSE for an agent framework
──────────────────────────────────────────────
SSE is unidirectional (server → client only).  Every HITL response
(tool approvals, human answers) required a separate HTTP POST, leading to
two concurrent HTTP connections per chat turn.

WebSocket gives a single persistent connection that handles:
  - Streaming agent output (text_delta, reasoning_delta, tool_call, …)
  - Tool approval requests + user responses
  - Human-input requests + user answers
  - Cancel commands
  - Heartbeat ping/pong

Message protocol
─────────────────
Client → Server:
  {"type": "chat",          "message": "…", "thread_id": "…", "file_ids": [], "system_instructions": null}
  {"type": "tool_approval", "request_id": "…", "action": "allow"|"deny", "modified_arguments": null}
  {"type": "hitl_response", "request_id": "…", "selected_key": null, "freeform_text": "…"}
  {"type": "cancel"}
  {"type": "ping"}

Server → Client (all existing SSE types as JSON, plus):
  {"type": "pong"}
  {"type": "done"}
  {"type": "cancelled"}
  {"type": "error", "message": "…"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.core.agents.react_agent import ReActAgent
from agent_framework.configs.settings import settings
from agent_framework.core.messages import CompletionChunk, ReasoningDeltaChunk, TextDeltaChunk
from agent_framework.core.messages.client_messages import AssistantMessage, ToolExecutionResultMessage
from agent_framework.server.database import get_db
from agent_framework.server.hooks import ChatContext, hooks
from agent_framework.server.services import get_thread
from agent_framework.server.services.agent_service import (
    load_agent_for_thread,
    persist_assistant_message,
    persist_tool_result,
    persist_user_message,
    sync_new_messages_to_redis,
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

router = APIRouter(tags=["websocket"])


# ── Helpers shared with SSE chat route ───────────────────────────────────────

def _get_agent_deps(app_state: Any) -> dict:
    from agent_framework.extensions.tools.web_surfer import WebSurferTool
    tools = list(app_state.tools)
    if not any(isinstance(t, WebSurferTool) for t in tools):
        tools.append(WebSurferTool())
    return {
        "model_client": app_state.model_client,
        "tools": tools,
        "system_instructions": app_state.system_instructions,
        "tool_approval_handler": getattr(app_state, "tool_approval_handler", None),
        "tools_requiring_approval": getattr(app_state, "tools_requiring_approval", None),
        "tool_timeout": getattr(app_state, "tool_timeout", None),
    }


def _build_tool_meta_map(tools: list) -> dict:
    """Build a map of tool_name → { risk, color, ui? } for event enrichment."""
    meta_map: dict = {}
    for tool in tools:
        try:
            schema = tool.get_schema()
            entry: dict = {
                "risk": schema.risk,          # e.g. "safe" | "sensitive" | "critical"
                "color": getattr(tool, "risk", None) and tool.risk.color or "green",
            }
            if schema.meta and schema.meta.get("ui"):
                entry["ui"] = schema.meta["ui"]
            meta_map[schema.name] = entry
        except Exception:
            pass
    return meta_map


def _chunk_to_payload(
    chunk: Any,
    tool_meta_map: dict,
    session_factory: Any,
    thread_id: str,
) -> dict | None:
    """Convert an agent stream chunk to a JSON-serialisable dict, or None to skip."""
    if isinstance(chunk, TextDeltaChunk):
        return {"type": "text_delta", "content": chunk.text, "partial": True}

    if isinstance(chunk, ReasoningDeltaChunk):
        return {"type": "reasoning_delta", "content": chunk.text, "partial": True}

    if isinstance(chunk, CompletionChunk):
        message = chunk.message
        serialized_tool_calls = None
        if message.tool_calls:
            serialized_tool_calls = []
            for tc in message.tool_calls:
                tc_data: dict = {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                meta = tool_meta_map.get(tc.name)
                if meta:
                    # Inject risk colour badge
                    tc_data["risk"]  = meta.get("risk", "safe")
                    tc_data["color"] = meta.get("color", "green")
                    ui_info = meta.get("ui")
                    if ui_info:
                        resource_uri = ui_info.get("resourceUri", "")
                        http_url = resolve_ui_uri(resource_uri) if resource_uri else None
                        tc_data["_meta"] = {"ui": {"resourceUri": resource_uri, "httpUrl": http_url or resource_uri}}
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

    if isinstance(chunk, ToolExecutionResultMessage):
        content_text = ""
        if isinstance(chunk.content, list):
            content_text = "\n".join(
                b.get("text", "") for b in chunk.content
                if isinstance(b, dict) and b.get("type") == "text"
            )
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
            "is_error": getattr(chunk, "isError", False),
            "has_app": "ui" in tool_meta,
            "http_url": tool_http_url,
            "app_data": getattr(chunk, "app_data", None),
            "risk":  tool_meta.get("risk", "safe"),
            "color": tool_meta.get("color", "green"),
            "partial": False,
        }

    return {"type": "unknown", "content": str(chunk), "partial": True}


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/chat")
async def ws_chat(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    """Full-duplex WebSocket chat endpoint.

    Connection lifecycle:
      1. Client connects to ws://<host>/ws/chat
      2. Client sends initial {"type":"chat", ...} message
      3. Server streams agent output as JSON messages
      4. Client can send HITL responses on the same connection at any time
      5. Connection closes when agent is done or client disconnects
    """
    await websocket.accept()

    try:
        # ── 1. Wait for initial chat message ────────────────────────────────
        try:
            init = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "error", "message": "Connection timeout — send chat message within 30s"})
            return

        if init.get("type") != "chat":
            await websocket.send_json({"type": "error", "message": "First message must have type='chat'"})
            return

        thread_id = init.get("thread_id")
        user_message = init.get("message", "")
        file_ids = init.get("file_ids") or []
        custom_instructions = init.get("system_instructions") or ""

        # ── 2. Validate thread ──────────────────────────────────────────────
        thread = await get_thread(db, thread_id)
        if not thread:
            await websocket.send_json({"type": "error", "message": "Thread not found"})
            return

        # ── 3. Build agent + per-connection HITL bridge ─────────────────────
        deps = _get_agent_deps(websocket.app.state)

        if custom_instructions.strip():
            deps["system_instructions"] = (
                deps["system_instructions"]
                + "\n\n---\n**Additional instructions from user:**\n"
                + custom_instructions.strip()
            )

        # Each WS connection gets its own bridge — no shared state
        bridge = WebHITLBridge()

        agent, snapshot_count = await load_agent_for_thread(
            db,
            thread_id,
            model_client=deps["model_client"],
            tools=deps["tools"],
            system_instructions=deps["system_instructions"],
            redis_memory=getattr(websocket.app.state, "redis_memory", None),
            model_context_window=settings.MODEL_CONTEXT_WINDOW,
            tool_approval_handler=bridge.approval_handler,
            tools_requiring_approval=deps.get("tools_requiring_approval"),
            tool_timeout=deps.get("tool_timeout"),
        )

        # ── 4. Attach file context ──────────────────────────────────────────
        if file_ids:
            elements = await get_files_by_ids(db, file_ids, thread_id)
            if elements:
                text_parts, image_notes = [], []
                for el in elements:
                    extracted = extract_text(el)
                    if extracted is not None:
                        text_parts.append(f"### File: {el.name}\n```\n{extracted}\n```")
                    elif (el.mime or "").startswith("image/"):
                        image_notes.append(f"[Image: {el.name}]")
                    else:
                        text_parts.append(f"### File: {el.name} (binary — available at /data/{el.name})")

                ci_client = getattr(websocket.app.state, "ci_client", None)
                if ci_client:
                    import base64 as _b64
                    for el in elements:
                        if el.content:
                            try:
                                await ci_client.write_file(thread_id, path=f"/data/{el.name}", content=_b64.b64encode(el.content).decode(), encoding="base64")
                            except Exception as e:
                                logger.warning("Failed to push file to CI: %s", e)

                if text_parts:
                    user_message = f"Files:\n\n" + "\n\n".join(text_parts) + f"\n\n---\n\n{user_message}"
                if image_notes:
                    user_message = "\n".join(image_notes) + "\n\n" + user_message

        # ── 5. Hooks + persist ──────────────────────────────────────────────
        ctx = ChatContext(thread_id=thread_id, db=db, agent=agent)
        await hooks.fire_message(ctx, user_message)
        await persist_user_message(db, thread_id, user_message)
        await db.commit()

        # ── 6. Set up workers ───────────────────────────────────────────────
        merged_queue: asyncio.Queue = asyncio.Queue()
        cancel_event = asyncio.Event()
        tool_meta_map = _build_tool_meta_map(deps["tools"])

        current_thread_id.set(thread_id)

        async def agent_worker():
            try:
                async for chunk in agent.run_stream(user_message):
                    await merged_queue.put(("agent", chunk))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                merged_queue.put_nowait(("error", str(e)))
            finally:
                merged_queue.put_nowait(("agent_done", None))

        async def hitl_worker():
            while True:
                event = await bridge.get_event()
                if event is _DONE:
                    break
                await merged_queue.put(("hitl", event))

        agent_task = asyncio.create_task(agent_worker())
        hitl_task = asyncio.create_task(hitl_worker())

        # ── 7. Send loop — drains merged_queue → WebSocket ──────────────────
        async def send_loop():
            bridge_signaled = False
            while True:
                try:
                    source, data = await asyncio.wait_for(merged_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    if cancel_event.is_set():
                        if not agent_task.done():
                            agent_task.cancel()
                            try:
                                await asyncio.wait_for(asyncio.shield(agent_task), timeout=3.0)
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                        if not bridge_signaled:
                            bridge_signaled = True
                            await bridge.signal_done()
                        await websocket.send_json({"type": "cancelled"})
                        return
                    continue

                try:
                    if source == "agent":
                        payload = _chunk_to_payload(data, tool_meta_map, websocket.app.state.session_factory, thread_id)
                        if payload:
                            # Persist assistant messages and tool results inline
                            if payload["type"] == "completion":
                                try:
                                    async with websocket.app.state.session_factory() as persist_db:
                                        await persist_assistant_message(persist_db, thread_id, data.message, tool_meta_map=tool_meta_map)
                                        await persist_db.commit()
                                except Exception:
                                    logger.exception("Failed to persist assistant message")
                            elif payload["type"] == "tool_result":
                                try:
                                    async with websocket.app.state.session_factory() as persist_db:
                                        await persist_tool_result(persist_db, thread_id, tool_call_id=payload["tool_call_id"], tool_name=payload["tool_name"], output=payload["content"], is_error=payload["is_error"])
                                        await persist_db.commit()
                                except Exception:
                                    logger.exception("Failed to persist tool result")
                            await websocket.send_json(json.loads(json.dumps(payload, default=str)))

                    elif source == "hitl":
                        await websocket.send_json(json.loads(json.dumps(data, default=str)))

                    elif source == "error":
                        await websocket.send_json({"type": "error", "message": str(data)})
                        return

                    elif source == "agent_done":
                        if not bridge_signaled:
                            bridge_signaled = True
                            await bridge.signal_done()
                        # Sync new messages from this turn back to Redis
                        redis_mem = getattr(websocket.app.state, "redis_memory", None)
                        if redis_mem is not None:
                            await sync_new_messages_to_redis(
                                redis_mem,
                                str(thread_id),
                                agent,
                                snapshot_count,
                            )
                        await websocket.send_json({"type": "done"})
                        return

                except Exception as e:
                    await websocket.send_json({"type": "error", "message": str(e)})

        # ── 8. Receive loop — WebSocket → bridge.resolve / cancel ───────────
        async def receive_loop():
            while True:
                try:
                    msg = await websocket.receive_json()
                    msg_type = msg.get("type", "")

                    if msg_type == "tool_approval":
                        # {"type": "tool_approval", "request_id": "...", "action": "allow"|"deny", "modified_arguments": null}
                        bridge.resolve(msg["request_id"], {
                            "action": msg.get("action", "deny"),
                            "modified_arguments": msg.get("modified_arguments"),
                            "reason": msg.get("reason", ""),
                        })

                    elif msg_type == "hitl_response":
                        # {"type": "hitl_response", "request_id": "...", "selected_key": null, "freeform_text": "..."}
                        bridge.resolve(msg["request_id"], {
                            "selected_key": msg.get("selected_key"),
                            "selected_label": msg.get("selected_label", ""),
                            "freeform_text": msg.get("freeform_text"),
                        })

                    elif msg_type == "cancel":
                        cancel_event.set()
                        return

                    elif msg_type == "ping":
                        await websocket.send_json({"type": "pong"})

                except WebSocketDisconnect:
                    cancel_event.set()
                    return
                except Exception:
                    return

        # ── 9. Run send + receive concurrently ──────────────────────────────
        send_task = asyncio.create_task(send_loop())
        recv_task = asyncio.create_task(receive_loop())

        try:
            done, pending = await asyncio.wait(
                [send_task, recv_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel whatever is still running
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            # Re-raise unexpected exceptions from completed tasks
            for task in done:
                if not task.cancelled():
                    exc = task.exception()
                    if exc:
                        logger.error("ws_chat task raised: %s", exc)
        finally:
            for task in (agent_task, hitl_task):
                if not task.done():
                    task.cancel()
            websocket.app.state.cancel_registry.pop(thread_id, None)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected cleanly")
    except Exception:
        logger.exception("Unexpected error in ws_chat")
        try:
            await websocket.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
