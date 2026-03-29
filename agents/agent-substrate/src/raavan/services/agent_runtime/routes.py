"""Agent Runtime — HTTP routes.

Routes:
  POST /agent/run       – start an agent run (called by Workflow Orchestrator)
  GET  /agent/health    – health check
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from raavan.services.agent_runtime.runner import (
    create_agent,
    load_memory_for_thread,
    run_agent_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent-runtime"])


class RunRequest(BaseModel):
    run_id: str
    thread_id: str
    user_content: str
    system_instructions: Optional[str] = None
    file_ids: list[str] | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str


@router.post("/run", status_code=202)
async def start_agent_run(body: RunRequest, request: Request):
    """Accept a run command and execute the agent asynchronously.

    The agent publishes events via the event bus as it runs.
    Returns immediately with 202 Accepted.
    """
    model_client = request.app.state.model_client
    tools = request.app.state.tools
    redis_memory = request.app.state.redis_memory
    event_bus = request.app.state.event_bus
    conversation_url = request.app.state.conversation_service_url

    system_instructions = (
        body.system_instructions or request.app.state.system_instructions
    )

    # Load memory
    memory = await load_memory_for_thread(
        thread_id=body.thread_id,
        system_instructions=system_instructions,
        redis_memory=redis_memory,
        conversation_service_url=conversation_url,
    )

    # Create agent
    agent = create_agent(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
    )

    # Run asynchronously
    asyncio.create_task(
        run_agent_stream(
            agent=agent,
            user_content=body.user_content,
            run_id=body.run_id,
            thread_id=body.thread_id,
            event_bus=event_bus,
        )
    )

    return RunResponse(run_id=body.run_id, status="accepted")
