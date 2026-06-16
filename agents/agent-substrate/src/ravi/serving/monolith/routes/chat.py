"""Chat streaming endpoint with HITL support.

POST /chat – send a message, receive SSE stream of agent response
including tool approval requests, human input requests, and tool results.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ravi.config import settings
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
from ravi.infrastructure.serving_factory import build_agent_for_thread, build_chat_tools

# current_thread_id: ContextVar that scopes TaskManagerTool to the active thread.
# Defined in capabilities; both serving and capabilities need it — tracked as an
# explicit exception in the import-linter "serving boundary" contract.
from ravi.capabilities.tools.task_manager.tool import current_thread_id
from ravi.kernel import ChatMessage
from ravi.kernel.core.content import TextBlock, ToolUseBlock
from ravi.kernel.core.content import (
    ChatMessage as _ChatMessage,
    Role,
    TextBlock as _TextBlock,
)
from ravi.kernel.core.identity import AgentId as _AgentId
from ravi.kernel.messaging.message import (
    ChatPayload as _ChatPayload,
    Message as _Message,
)
from ravi.serving.monolith.dependencies import ServerDependencies, get_ctx
from ravi.serving.monolith.database import get_db
from ravi.serving.monolith.hooks import ChatContext, hooks
from ravi.serving.monolith.schemas import ChatRequest
from ravi.serving.monolith.services import get_thread
from ravi.serving.monolith.services.agent_service import (
    persist_assistant_message,
    persist_tool_result,
    persist_user_message,
)
from ravi.serving.monolith.security.deps import get_current_user
from ravi.serving.monolith.sse.bridge import WebHITLBridge
from ravi.serving.protocol import (
    PROTOCOL_VERSION,
    TurnCompletedEvent,
    ToolResultEvent,
)
from ravi.serving.stream import AgentStreamSession

logger = setup_logging()


@dataclass
class _ImagePayload:
    """Raw image binary for multimodal user messages."""

    data: bytes
    media_type: str


MediaType = str | _ImagePayload

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

# Stronger phrases that warrant forcing manage_tasks as the first tool call
# (model may ignore system prompt instructions on weaker models).
TASK_FORCE_PHRASES = (
    "plan tasks",
    "plan the tasks",
    "create tasks",
    "create a task",
    "task list",
    "todo list",
    "to-do list",
    "checklist for",
    "plan for",
    "plan a ",
    "plan the ",
    "plan to ",
    "plan my ",
    "organise tasks",
    "organize tasks",
    "break down",
    "break this down",
    "step by step plan",
    "steps to ",
    "steps for ",
    "roadmap for",
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


def _should_force_task_planning(user_text: str) -> bool:
    """Return True when the request clearly asks to create a task list.

    Used to force manage_tasks as the initial tool call so weaker models
    don't ignore the system prompt instruction.
    """
    normalized = user_text.lower()

    # Explicit kanban/board request
    if "kanban" in normalized or "task board" in normalized:
        return True

    # Phrase-based match
    if any(phrase in normalized for phrase in TASK_FORCE_PHRASES):
        return True

    # Numbered task list pattern: "1. foo 2. bar 3. baz"
    # Matches when the user pastes/types a numbered list of 2+ items
    if len(re.findall(r"\b\d+[\.\)]\s+\S", user_text)) >= 2:
        return True

    return False


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


async def _get_agent_deps(ctx: ServerDependencies, thread_id: str):
    """Assemble per-request agent dependencies with an isolated HITL bridge."""
    bridge = await ctx.bridge_registry.acquire(str(thread_id))
    tools = build_chat_tools(ctx.tools, bridge)
    return {
        "model_client": ctx.model_client,
        "tools": tools,
        "system_instructions": ctx.system_instructions,
        "tools_requiring_approval": ctx.tools_requiring_approval,
        "tool_timeout": ctx.tool_timeout,
        "bridge": bridge,
        "runtime": ctx.runtime,
    }


def _build_tool_meta_map(tools: list) -> dict:
    """Build a mapping of tool_name → { risk, color, ui? } for event enrichment."""
    from ravi.kernel.tools import ToolRisk

    meta_map: dict = {}
    for tool in tools:
        name = getattr(tool, "name", None)
        if not name:
            continue
        risk = getattr(tool, "risk", ToolRisk.SAFE)
        color = (
            "red"
            if risk == ToolRisk.CRITICAL
            else "yellow"
            if risk == ToolRisk.HIGH
            else "green"
        )
        entry: dict = {"risk": str(risk), "color": color}
        ui = getattr(tool, "ui", None)
        if ui:
            entry["ui"] = ui
        meta_map[name] = entry
    return meta_map


class _WirePersister:
    """Persists wire events to Postgres inline as the run streams.

    Implements the ``stream.Persister`` protocol. ``persist_turn`` writes the
    assistant message (text + tool calls, enriched with MCP-App UI metadata via
    ``tool_meta_map``); ``persist_tool`` records error tool results so reloads
    can show failures. Each write opens its own DB session so a slow write never
    blocks the stream's own transaction.
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        thread_id: Any,
        tool_meta_map: dict,
        attachments: list | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._thread_id = thread_id
        self._tool_meta_map = tool_meta_map
        self._attachments = attachments or []

    async def persist_turn(self, event: TurnCompletedEvent) -> None:
        content: list[Any] = []
        if event.text:
            content.append(TextBlock(text=event.text))
        for tc in event.tool_calls:
            content.append(
                ToolUseBlock(call_id=tc.id, tool_name=tc.name, arguments=tc.args)
            )
        if not content:
            return
        message = ChatMessage(role="assistant", content=content)
        metadata = {"attachments": self._attachments} if self._attachments else None
        try:
            async with self._session_factory() as db:
                await persist_assistant_message(
                    db,
                    self._thread_id,
                    message,
                    tool_meta_map=self._tool_meta_map,
                    metadata=metadata,
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to persist assistant turn")

    async def persist_tool(self, event: ToolResultEvent) -> None:
        if event.ok:
            return  # successful results are reconstructed from the assistant turn
        try:
            async with self._session_factory() as db:
                await persist_tool_result(
                    db,
                    self._thread_id,
                    event.call_id,
                    event.tool_name,
                    event.error or "",
                    is_error=True,
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to persist tool result")


async def _build_file_context(
    db: AsyncSession,
    body: ChatRequest,
    request: Request,
    ctx: ServerDependencies,
) -> tuple[str, list[_ImagePayload], list[dict[str, Any]]]:
    """Resolve file_ids to text/image/attachment context for the chat turn."""
    if not body.file_ids or ctx.file_store is None:
        return "", [], []

    from sqlalchemy import select

    from ravi.serving.monolith.models import FileMetadata

    rows = (
        (
            await db.execute(
                select(FileMetadata).where(
                    FileMetadata.id.in_(body.file_ids),
                    FileMetadata.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    text_parts: list[str] = []
    image_inputs: list[_ImagePayload] = []
    attachments: list[dict[str, Any]] = []

    for meta in rows:
        data = await ctx.file_store.download(meta.object_key)
        if meta.content_type.startswith("image/"):
            image_inputs.append(_ImagePayload(data=data, media_type=meta.content_type))
        elif meta.content_type.startswith("text/"):
            text_parts.append(
                f"[File: {meta.original_name}]\n"
                + data.decode("utf-8", errors="replace")
            )
        else:
            attachments.append(
                {
                    "id": str(meta.id),
                    "name": meta.original_name,
                    "mime": meta.content_type,
                    "size": meta.size_bytes,
                }
            )

    return "\n\n".join(text_parts), image_inputs, attachments


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    ctx: ServerDependencies = Depends(get_ctx),
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
        # If so, we avoid forcing a hard reset/creation, and instead nudge the model
        # to update or continue it.
        has_existing_tasks = False
        if allow_task_planning:
            _store = request.app.state.task_tool.store
            _existing = await _store.get_by_conversation(str(body.thread_id))
            if _existing:
                has_existing_tasks = True
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
        if (
            not initial_tool_choice
            and allow_task_planning
            and not has_existing_tasks
            and _should_force_task_planning(display_content)
        ):
            initial_tool_choice = "manage_tasks"
            deps["system_instructions"] = (
                deps["system_instructions"]
                + "\n\n---\n**Task Planning — call manage_tasks NOW:**\n"
                + f'The user said: "{display_content}"\n'
                + "Call manage_tasks action=create_list with 5-8 specific, actionable task titles "
                + "that reflect EXACTLY what the user asked for above. "
                + "Use concrete titles like the real steps someone would do for this request. "
                + "Do NOT use generic placeholders like 'Identify tasks', 'Complete kanban tasks', or 'Plan approach'. "
                + "After creating the list, call start_task before each step and complete_task after."
            )
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
    # Per-thread HITL bridge (acquired in _get_agent_deps).
    bridge: WebHITLBridge = deps["bridge"]

    # Tool risk/UI metadata, built in setup so a failure releases the lock here
    # (not inside the generator where it could orphan the lock).
    tool_meta_map = _build_tool_meta_map(deps["tools"])

    # Per-request cancel signal — set by POST /chat/{thread_id}/cancel.
    cancel_event: asyncio.Event = asyncio.Event()
    ctx.cancel_registry[str(body.thread_id)] = cancel_event

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
        "system_instructions": deps["system_instructions"],
        "tool_names": [getattr(t, "name", "") for t in deps["tools"]],
        "max_iterations": 30,
        "session_id": str(body.thread_id),
        "model_context_window": settings.MODEL_CONTEXT_WINDOW,
    }
    session = AgentStreamSession(
        runtime=deps["runtime"],
        agent=agent,
        msg=_entry_msg,
        bridge=bridge,
        is_disconnected=request.is_disconnected,
        cancel_event=cancel_event,
        persister=persister,
        spec=_agent_spec,
    )

    async def sse_generator() -> AsyncIterator[str]:
        """Serialize the session's WireEvents as SSE `data:` lines.

        All concurrency (agent run, HITL merge, cancel/disconnect, persistence)
        lives in `AgentStreamSession`; this only frames events for the transport
        and guarantees the thread lock is released exactly once.
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
            ctx.cancel_registry.pop(str(body.thread_id), None)
            thread_lock.release()
            ctx.thread_locks.pop(str(body.thread_id), None)
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
