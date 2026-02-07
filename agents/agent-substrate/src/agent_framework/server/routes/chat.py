"""Chat streaming endpoint.

POST /chat – send a message, receive SSE stream of agent response.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.agents.react_agent import ReActAgent
from agent_framework.messages import CompletionChunk, ReasoningDeltaChunk, TextDeltaChunk
from agent_framework.messages.client_messages import AssistantMessage
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
from agent_framework.tools.web_surfer import WebSurferTool

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
    }


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Stream agent response as Server-Sent Events.

    Flow:
      1. Validate thread exists
      2. Rebuild agent memory from DB
      3. Fire on_message hook
      4. Persist user message to DB
      5. Stream agent response (yielding SSE events)
      6. Persist assistant response to DB
    """
    # 1. Validate thread
    thread = await get_thread(db, body.thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # 2. Build agent with restored memory
    deps = _get_agent_deps(request)
    agent = await load_agent_for_thread(
        db,
        body.thread_id,
        model_client=deps["model_client"],
        tools=deps["tools"],
        system_instructions=deps["system_instructions"],
    )

    # 3. Extract user content from last message
    user_content = body.messages[-1].content

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

    async def sse_generator() -> AsyncIterator[str]:
        """Yield SSE events from agent stream, persist results after."""
        final_message: AssistantMessage | None = None

        try:
            async for chunk in agent.run_stream(user_content):
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

                    payload = {
                        "type": "completion",
                        "role": message.role,
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                            }
                            for tc in message.tool_calls
                        ]
                        if message.tool_calls
                        else None,
                        "finish_reason": message.finish_reason,
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

        except Exception as e:
            logger.exception("Error during agent streaming")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        # Persist final assistant message to DB
        if final_message is not None:
            try:
                async with request.app.state.session_factory() as persist_db:
                    await persist_assistant_message(
                        persist_db,
                        body.thread_id,
                        final_message,
                    )
                    await persist_db.commit()
            except Exception:
                logger.exception("Failed to persist assistant message")

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        content=sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
