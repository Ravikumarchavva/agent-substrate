"""Agent Runtime — HTTP routes.

Routes:
  POST /agent/run       – start an agent run (called by Workflow Orchestrator)
  GET  /agent/health    – health check
"""

from __future__ import annotations
from substrate.logger import setup_logging

import asyncio
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from substrate.infrastructure.serving_factory import build_memory_tool
from substrate.serving.services.agent_runtime.service import (
    create_agent,
    execute_agent_run,
    load_memory_for_thread,
)

logger = setup_logging()

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
    history = request.app.state.history
    short_term_memory = request.app.state.short_term_memory
    event_bus = request.app.state.event_bus
    runtime = request.app.state.runtime
    conversation_url = request.app.state.conversation_service_url

    system_instructions = (
        body.system_instructions or request.app.state.system_instructions
    )

    # Load memory
    memory = await load_memory_for_thread(
        thread_id=body.thread_id,
        system_instructions=system_instructions,
        history=history,
        conversation_service_url=conversation_url,
    )

    memory_tool = build_memory_tool(body.thread_id, short_term_memory)
    if memory_tool is not None:
        tools = [*tools, memory_tool]

    # Create agent
    agent = create_agent(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        session_id=body.thread_id,
    )

    forwarding_tasks: dict = request.app.state.forwarding_tasks
    run_id = body.run_id

    if run_id in forwarding_tasks and not forwarding_tasks[run_id].done():
        logger.warning(
            "Forwarding task already running for run %s — skipping duplicate", run_id
        )
        return RunResponse(run_id=run_id, status="accepted")

    task = asyncio.create_task(
        execute_agent_run(
            agent=agent,
            user_content=body.user_content,
            run_id=run_id,
            thread_id=body.thread_id,
            event_bus=event_bus,
            runtime=runtime,
        ),
        name=f"forward-{run_id}",
    )
    forwarding_tasks[run_id] = task

    def _on_done(t: asyncio.Task) -> None:
        forwarding_tasks.pop(run_id, None)
        if not t.cancelled() and t.exception():
            logger.error("Forwarding task for run %s failed: %s", run_id, t.exception())

    task.add_done_callback(_on_done)

    return RunResponse(run_id=run_id, status="accepted")
