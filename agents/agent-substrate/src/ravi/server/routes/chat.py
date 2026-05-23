"""Chat streaming endpoint with HITL support.

POST /chat – send a message, receive SSE stream of agent response
including tool approval requests, human input requests, and tool results.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ravi.configs.settings import settings
from ravi.integrations.llm.factory import (
    CHAT_MODEL_FALLBACKS,
    create_model_client,
    detect_provider,
    has_provider_api_key,
    model_supports_vision,
    resolve_model_for_available_credentials,
    resolve_vision_model_for_available_credentials,
    strip_provider_prefix,
)
from ravi.core.messages import (
    ImageContent,
    MediaType,
    ReasoningDeltaChunk,
    TextDeltaChunk,
)
from ravi.core.execution.context import ExecutionContext
from ravi.core.messages.client_messages import (
    AssistantMessage,
    ToolExecutionResultMessage,
)
from ravi.shared.execution import stream_agent_run
from ravi.server.context import ServerContext, get_ctx
from ravi.server.database import get_db
from ravi.server.hooks import ChatContext, hooks
from ravi.server.schemas import ChatRequest
from ravi.server.services import get_thread
from ravi.server.services.agent_service import (
    load_agent_for_thread,
    persist_assistant_message,
    persist_tool_result,
    persist_user_message,
)
from ravi.server.services.file_service import (
    extract_text,
    get_file_content,
    get_files_by_ids,
)
from ravi.server.routes.mcp_apps import resolve_ui_uri
from ravi.catalog.tools.task_manager.tool import current_thread_id
from ravi.catalog.tools.file_manager.tool import (
    current_thread_id as file_thread_id,
)
from ravi.catalog.tools.web_surfer.tool import WebSurferTool
from ravi.catalog.tools.human_input.tool import AskHumanTool
from ravi.server.security.deps import get_current_user
from ravi.server.sse.bridge import BRIDGE_DONE, BridgeRegistry, WebHITLBridge
from ravi.server.sse.events import (
    EventBus,
    BUS_CLOSED,
    TextDeltaEvent,
    ReasoningDeltaEvent,
    ErrorEvent,
    RawDictEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"], dependencies=[Depends(get_current_user)])

ATTACHMENT_ANALYSIS_INSTRUCTIONS = (
    "When the user asks about attached files, images, or documents, inspect the "
    "attachment directly and answer in a normal assistant response. Avoid "
    "creating task lists, plans, or workflow-style tool loops unless the user "
    "explicitly asks for planning, task tracking, or automation. "
    "When presenting structured data, always use proper Markdown tables with "
    "pipe (|) syntax and header separator rows (|---|). Never use plain text "
    "or HTML tags like <br> for tabular data."
)

ATTACHMENT_PLANNING_KEYWORDS = (
    "plan",
    "planning",
    "task",
    "tasks",
    "todo",
    "to-do",
    "checklist",
    "workflow",
    "steps",
    "roadmap",
    "organize",
    "organise",
)

WORKSPACE_MAIL_NOUNS = (
    "email",
    "emails",
    "mail",
    "mails",
    "gmail",
    "inbox",
    "mailbox",
)

WORKSPACE_MAIL_ACTIONS = (
    "summarize",
    "summarise",
    "analyze",
    "analyse",
    "review",
    "check",
    "read",
    "scan",
    "show",
    "list",
)

WORKSPACE_MAIL_TOOL_NAMES = {"ask_human", "google_workspace"}

WORKSPACE_MAIL_INSTRUCTIONS = (
    "If the user asks about their Gmail, inbox, or recent emails and the "
    "google_workspace tool is available, you must call google_workspace before "
    "answering. Do not claim you lack inbox access and do not ask the user to "
    "paste emails until after the tool has been attempted. For requests about "
    "recent or latest emails, call google_workspace with service='gmail' and an "
    "empty query string, then summarize the five most recent relevant messages "
    "from the tool output unless the user asked for a different number."
)

WORKSPACE_CALENDAR_NOUNS = (
    "calendar",
    "event",
    "meeting",
    "appointment",
    "schedule",
    "reminder",
)

WORKSPACE_CALENDAR_WRITE_ACTIONS = (
    "create",
    "add",
    "schedule",
    "set up",
    "make",
    "book",
    "cancel",
    "delete",
    "remove",
)

WORKSPACE_CALENDAR_WRITE_INSTRUCTIONS = (
    "The user wants to create or cancel a calendar event using Google Calendar. "
    "Use the google_workspace tool with action='create_event' or action='cancel_event'. "
    "For create_event, provide title, start_time as ISO 8601 with timezone offset "
    "(e.g. '2026-04-20T19:00:00+05:30' for 7 PM IST), and optionally end_time. "
    "IST is UTC+05:30. If no end time is specified, end_time may be omitted (defaults to 1 hour after start). "
    "For cancel_event, you must first call google_workspace with service='calendar' "
    "to list events and find the event_id, then call again with action='cancel_event'."
)


def _tool_name(tool: Any) -> str:
    try:
        return tool.get_schema().name
    except Exception:
        return str(getattr(tool, "name", ""))


def _should_allow_task_planning(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(keyword in normalized for keyword in ATTACHMENT_PLANNING_KEYWORDS)


def _should_route_workspace_mail_request(user_text: str) -> bool:
    normalized = user_text.lower()
    if not any(keyword in normalized for keyword in WORKSPACE_MAIL_NOUNS):
        return False

    if any(keyword in normalized for keyword in WORKSPACE_MAIL_ACTIONS):
        return True

    return any(keyword in normalized for keyword in ("recent", "latest", "last "))


def _configure_workspace_mail_request(
    user_text: str,
    tools: list[Any],
    system_instructions: str,
) -> tuple[list[Any], str, str | None]:
    if not _should_route_workspace_mail_request(user_text):
        return tools, system_instructions, None

    if not any(_tool_name(tool) == "google_workspace" for tool in tools):
        return tools, system_instructions, None

    routed_tools = [
        tool for tool in tools if _tool_name(tool) in WORKSPACE_MAIL_TOOL_NAMES
    ]
    updated_instructions = (
        system_instructions
        + "\n\n---\n**Google Workspace mail instructions:**\n"
        + WORKSPACE_MAIL_INSTRUCTIONS
    )
    return routed_tools, updated_instructions, "google_workspace"


def _should_route_calendar_write_request(user_text: str) -> bool:
    normalized = user_text.lower()
    if not any(noun in normalized for noun in WORKSPACE_CALENDAR_NOUNS):
        return False
    return any(action in normalized for action in WORKSPACE_CALENDAR_WRITE_ACTIONS)


def _configure_calendar_write_request(
    user_text: str,
    tools: list[Any],
    system_instructions: str,
) -> tuple[list[Any], str, str | None]:
    if not _should_route_calendar_write_request(user_text):
        return tools, system_instructions, None

    if not any(_tool_name(tool) == "google_workspace" for tool in tools):
        return tools, system_instructions, None

    routed_tools = [
        tool for tool in tools if _tool_name(tool) in WORKSPACE_MAIL_TOOL_NAMES
    ]
    updated_instructions = (
        system_instructions
        + "\n\n---\n**Google Workspace calendar instructions:**\n"
        + WORKSPACE_CALENDAR_WRITE_INSTRUCTIONS
    )
    return routed_tools, updated_instructions, "google_workspace"


def _serialize_attached_file(meta: Any) -> dict[str, Any]:
    """Return a JSON-safe attachment descriptor for message metadata."""
    props = meta.props or {}
    return {
        "id": str(meta.id),
        "thread_id": str(meta.thread_id) if meta.thread_id else None,
        "name": meta.original_name,
        "mime": meta.content_type,
        "size": meta.size_bytes,
        "document_type": props.get("document_type"),
        "document_class": props.get("document_class"),
    }


async def _get_agent_deps(ctx: ServerContext, thread_id: str):
    """Assemble per-request agent dependencies with an isolated HITL bridge."""
    bridge_registry: BridgeRegistry = ctx.bridge_registry
    bridge = await bridge_registry.acquire(str(thread_id))

    # Build a fresh AskHumanTool for this request wired to the thread's bridge.
    # Removes the placeholder from ctx.tools so only one instance exists.
    base_tools = [t for t in ctx.tools.all_tools() if not isinstance(t, AskHumanTool)]
    ask_tool = AskHumanTool(
        handler=bridge.human_handler,
        max_requests_per_run=5,
    )
    tools = [ask_tool] + base_tools

    # Only add WebSurferTool if not already present
    if not any(isinstance(t, WebSurferTool) for t in tools):
        try:
            tools.append(WebSurferTool())
        except Exception:
            logger.debug("WebSurferTool not available for this request")

    return {
        "model_client": ctx.model_client,
        "tools": tools,
        "system_instructions": ctx.system_instructions,
        "tool_approval_handler": bridge.approval_handler,
        "tools_requiring_approval": ctx.tools_requiring_approval,
        "tool_timeout": ctx.tool_timeout,
        "bridge": bridge,
        "runtime": ctx.runtime,
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
            logger.warning(
                "Failed to get schema for tool %s: %s",
                getattr(tool, "name", "unknown"),
                e,
            )
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
                tc_data["risk"] = meta.get("risk", "safe")
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
        }
        if message.usage
        else None,
        "partial": False,
        "complete": True,
    }


def _build_tool_result_payload(
    chunk: ToolExecutionResultMessage, tool_meta_map: dict
) -> dict:
    """Build the SSE ``tool_result`` event payload from a ``ToolExecutionResultMessage``."""
    content_text = ""
    if isinstance(chunk.content, list):
        parts = []
        for block in chunk.content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(getattr(block, "text") or "")
            elif isinstance(block, str):
                parts.append(block)
        content_text = "\n".join(parts)

    tool_name = getattr(chunk, "name", "unknown")
    tool_meta = tool_meta_map.get(tool_name, {})
    tool_http_url = ""
    if "ui" in tool_meta:
        ui_info = tool_meta["ui"]
        resource_uri = ui_info.get("resourceUri", "")
        tool_http_url = (
            resolve_ui_uri(resource_uri) if resource_uri else f"/ui/{tool_name}"
        )

    return {
        "type": "tool_result",
        "tool_name": tool_name,
        "tool_call_id": getattr(chunk, "tool_call_id", ""),
        "content": content_text,
        "is_error": getattr(chunk, "is_error", False),
        "has_app": "ui" in tool_meta,
        "http_url": tool_http_url,
        "app_data": getattr(chunk, "app_data", None),
        "risk": tool_meta.get("risk", "safe"),
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
) -> tuple[str, list[ImageContent], list[dict[str, Any]]]:
    """Load file IDs from the request, extract text and push to CI VM.

    Returns:
        (file_context_block, image_inputs, attachments) where
        - file_context_block is a formatted string to prepend to the user
          message (empty string when no files were requested), and
        - image_inputs is a list of multimodal image payloads for the LLM, and
        - attachments contains JSON-safe file metadata for UI rendering.
    """
    if not body.file_ids:
        return "", [], []

    store = ctx.file_store
    if store is None:
        raise RuntimeError("File store is not configured")

    files = await get_files_by_ids(db, body.file_ids, body.thread_id)
    if not files:
        return "", [], []

    text_parts: list[str] = []
    image_inputs: list[ImageContent] = []
    attachments = [_serialize_attached_file(meta) for meta in files]

    for meta in files:
        extracted = await extract_text(store, meta)
        if extracted is not None:
            text_parts.append(f"### File: {meta.original_name}\n```\n{extracted}\n```")
        elif (meta.content_type or "").startswith("image/"):
            image_inputs.append(
                ImageContent(
                    data=await get_file_content(store, meta),
                    media_type=meta.content_type or "image/png",
                )
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
                logger.info(
                    "Pushed file %s (%d bytes) to CI session %s",
                    meta.original_name,
                    meta.size_bytes,
                    session_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to push %s to CI VM: %s", meta.original_name, exc
                )

    if not text_parts:
        if image_inputs:
            names = ", ".join(m.original_name for m in files)
            block = (
                f"The user attached {len(image_inputs)} image file(s): {names}. "
                "Use the attached image content directly when answering."
            )
            return block, image_inputs, attachments
        return "", image_inputs, attachments

    names = ", ".join(m.original_name for m in files)
    sections = "\n\n".join(text_parts)
    block = (
        f"The user has attached {len(files)} file(s): {names}.\n"
        f"File contents:\n\n{sections}"
    )
    return block, image_inputs, attachments


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
                    "GOOGLE_API_KEY or GEMINI_API_KEY, OPENAI_API_KEY, "
                    "or ANTHROPIC_API_KEY."
                ),
            )
        if image_inputs and not model_supports_vision(resolved_model):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Image uploads require a vision-capable chat model. "
                    "Configure GOOGLE_API_KEY or GEMINI_API_KEY, OPENAI_API_KEY, "
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
        if initial_tool_choice:
            logger.info(
                "Thread %s: forcing first tool choice to %s for mailbox request",
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
            runtime=deps["runtime"],
            enable_capability_search=False,
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
        tool_meta_map = _build_tool_meta_map(agent.tools)
        bus: EventBus = EventBus()
        bridge_signaled = False
        chat_run_id = str(uuid4())

        # Per-request cancel signal — set by POST /chat/{thread_id}/cancel
        # Key MUST be str to match cancel.py which receives thread_id as a path param.
        cancel_event: asyncio.Event = asyncio.Event()
        ctx.cancel_registry[str(body.thread_id)] = cancel_event

        async def agent_worker() -> None:
            """Run agent; emit typed events and persist inline to Postgres."""
            nonlocal bridge_signaled
            generated_files: list[dict] = []
            try:

                async def _emit_text_delta(chunk: TextDeltaChunk) -> None:
                    await bus.emit(TextDeltaEvent(content=chunk.text, partial=True))

                async def _emit_reasoning_delta(chunk: ReasoningDeltaChunk) -> None:
                    await bus.emit(
                        ReasoningDeltaEvent(content=chunk.text, partial=True)
                    )

                async def _emit_completion(message: AssistantMessage) -> None:
                    payload = _build_completion_payload(message, tool_meta_map)
                    metadata = None
                    if generated_files:
                        metadata = {"attachments": generated_files}
                        payload["attachments"] = generated_files

                    try:
                        async with ctx.session_factory() as persist_db:
                            await persist_assistant_message(
                                persist_db,
                                body.thread_id,
                                message,
                                tool_meta_map=tool_meta_map,
                                metadata=metadata,
                            )
                            await persist_db.commit()
                    except Exception:
                        logger.exception("Failed to persist assistant message")
                    await bus.emit_dict(payload)

                async def _emit_tool_result(
                    chunk: ToolExecutionResultMessage,
                ) -> None:
                    payload = _build_tool_result_payload(chunk, tool_meta_map)
                    raw_content = payload.pop("_raw_content", "")
                    try:
                        async with ctx.session_factory() as persist_db:
                            await persist_tool_result(
                                persist_db,
                                body.thread_id,
                                tool_call_id=getattr(chunk, "tool_call_id", ""),
                                tool_name=getattr(chunk, "name", "unknown"),
                                output=raw_content,
                                is_error=getattr(chunk, "is_error", False),
                            )

                            tool_media = getattr(chunk, "media", None)
                            if tool_media:
                                from ravi.server.services.file_service import save_file
                                from ravi.core.storage.tenant import FileScope
                                import uuid
                                import base64

                                for idx, media_item in enumerate(tool_media):
                                    filename = f"generated_plot_{idx + 1}.png"
                                    mime = media_item.media_type or "image/png"
                                    if "image/jpeg" in mime:
                                        filename = f"generated_plot_{idx + 1}.jpg"
                                    elif "image/webp" in mime:
                                        filename = f"generated_plot_{idx + 1}.webp"

                                    raw_data = media_item.data
                                    if isinstance(raw_data, str):
                                        try:
                                            raw_data = base64.b64decode(raw_data)
                                        except Exception:
                                            raw_data = raw_data.encode("utf-8")
                                    elif isinstance(raw_data, bytes):
                                        pass
                                    else:
                                        raw_data = str(raw_data).encode("utf-8")

                                    file_meta = await save_file(
                                        persist_db,
                                        ctx.file_store,
                                        thread_id=uuid.UUID(str(body.thread_id)),
                                        name=filename,
                                        mime=mime,
                                        content=raw_data,
                                        scope=FileScope.UPLOADS,
                                    )

                                    file_dict = {
                                        "id": str(file_meta.id),
                                        "thread_id": str(file_meta.thread_id),
                                        "name": file_meta.original_name,
                                        "mime": file_meta.content_type,
                                        "size": file_meta.size_bytes,
                                    }
                                    generated_files.append(file_dict)

                            await persist_db.commit()
                    except Exception:
                        logger.exception(
                            "Failed to persist tool result or process media"
                        )
                    await bus.emit_dict(payload)

                async def _emit_unknown(chunk: object) -> None:
                    await bus.emit_dict(
                        {"type": "unknown", "content": str(chunk), "partial": True}
                    )

                async def _emit_error(exc: Exception) -> None:
                    await bus.emit(ErrorEvent(message=str(exc)))

                await stream_agent_run(
                    agent=agent,
                    user_content=user_content,
                    input_content=user_input_content,
                    tool_choice=initial_tool_choice,
                    execution_context=ExecutionContext(
                        run_id=chat_run_id,
                        correlation_id=chat_run_id,
                        thread_id=str(body.thread_id),
                        input_text=user_content,
                    ),
                    on_text_delta=_emit_text_delta,
                    on_reasoning_delta=_emit_reasoning_delta,
                    on_completion=_emit_completion,
                    on_tool_result=_emit_tool_result,
                    on_unknown=_emit_unknown,
                    on_error=_emit_error,
                )

            except asyncio.CancelledError:
                raise
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
        current_thread_id.set(str(body.thread_id))
        file_thread_id.set(str(body.thread_id))

        agent_task = asyncio.create_task(agent_worker())
        hitl_task = asyncio.create_task(hitl_worker())

        try:
            while True:
                # Timeout-based poll so we can detect disconnect/cancel between events
                try:
                    item = await bus.poll(0.2)
                except asyncio.TimeoutError:
                    # ── Disconnect detection ─────────────────────────────────
                    if await request.is_disconnected():
                        logger.info("Client disconnected for thread %s", body.thread_id)
                        resolved = bridge.cancel_all_pending("session_disconnected")
                        if resolved:
                            logger.info(
                                "Thread %s: resolved %d pending HITL request(s) "
                                "with session_disconnected",
                                body.thread_id,
                                resolved,
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
                        break
                    continue

                # ── Bus closed = both workers finished normally ───────────────
                if item is BUS_CLOSED:
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
