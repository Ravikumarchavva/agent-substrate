"""Restate AgentWorkflow — durable ReAct loop with journal-based replay.

The four scenarios this enables:

1. **Double charge prevention** — ``ctx.run()`` journals each tool call;
   crash after charging card → replay skips charge, retries only email.
2. **HITL survival** — ``ctx.promise()`` persists approval state on disk;
   worker restart → promise survives, user approves when ready.
3. **Idempotency** — ``ctx.uuid()`` generates stable keys per journal
   position; same key on retry → downstream deduplicates.
4. **Parallel sub-agents** — journal tracks which sub-agents completed;
   crash → only retries the failed one.

Usage::

    # Start a workflow
    client = RestateClient(ingress_url="http://localhost:8080")
    wf_id = await client.start_workflow("thread-1", "Hello!", claims={})

    # Resolve HITL approval
    await client.resolve_promise(wf_id, "resolve_approval", {"action": "approve"})
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from restate import Workflow, WorkflowContext, WorkflowSharedContext

from raavan.distributed import activities
from raavan.distributed.policies import get_policy

logger = logging.getLogger(__name__)

# ── Workflow definition ──────────────────────────────────────────────────

agent_workflow = Workflow("AgentWorkflow")


@agent_workflow.main()
async def run(ctx: WorkflowContext, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entrypoint — a durable ReAct agent loop.

    Payload:
        thread_id: Conversation thread ID.
        user_content: User message text.
        system_instructions: System prompt for the agent.
        model: LLM model name (default ``"gpt-4o-mini"``).
        max_iterations: Maximum ReAct steps (default 30).
    """
    thread_id: str = payload["thread_id"]
    user_content: str = payload["user_content"]
    system_instructions: str = payload.get(
        "system_instructions", "You are a helpful agent."
    )
    model: str = payload.get("model", "gpt-4o-mini")
    max_iterations: int = payload.get("max_iterations", 30)

    # ── Step 0: Restore memory from Redis ────────────────────────────
    await ctx.run(
        "restore_memory",
        activities.restore_memory,
        args=(thread_id,),
    )

    # ── Step 1: Persist user message ─────────────────────────────────
    await ctx.run(
        "persist_user",
        activities.persist_message,
        args=(thread_id, "user", user_content),
    )

    # ── Step 2: ReAct loop ───────────────────────────────────────────
    tool_schemas = activities.get_tool_schemas()
    final_text: str = ""

    for step in range(max_iterations):
        # ── THINK: Call LLM ──────────────────────────────────────────
        llm_result: Dict[str, Any] = await ctx.run(
            f"llm_{step}",
            activities.do_llm_call,
            args=(thread_id, model, tool_schemas, system_instructions),
        )

        tool_calls = llm_result.get("tool_calls", [])

        # No tool calls → final answer
        if not tool_calls:
            final_text = llm_result.get("content") or ""
            break

        # Persist assistant message (with tool calls indication)
        assistant_text = llm_result.get("content") or ""
        if assistant_text:
            await ctx.run(
                f"persist_assistant_{step}",
                activities.persist_message,
                args=(thread_id, "assistant", assistant_text),
            )

        # ── ACT: Execute each tool call ──────────────────────────────
        for i, tc in enumerate(tool_calls):
            tc_name: str = tc["name"]
            tc_args: Dict[str, Any] = tc["arguments"]
            tc_id: str = tc["call_id"]
            policy = get_policy(tc_name)

            # ── HITL: human input request ────────────────────────────
            if policy.is_hitl_input:
                request_id = str(ctx.uuid())

                # Publish request event (side effect, ephemeral on replay)
                await ctx.run(
                    f"hitl_event_{step}_{i}",
                    _publish_hitl_event,
                    args=(thread_id, request_id, tc_name, tc_args),
                )

                # Suspend workflow — wait for human input
                human_response = await ctx.promise(f"human-{request_id}").value()

                # Persist the human's answer as tool result
                answer = human_response.get("response", "")
                await ctx.run(
                    f"persist_human_{step}_{i}",
                    activities.persist_tool_result,
                    args=(thread_id, tc_name, tc_id, answer, False),
                )
                continue

            # ── HITL: tool approval gate ─────────────────────────────
            if policy.requires_approval:
                request_id = str(ctx.uuid())

                # Publish approval request (side effect)
                await ctx.run(
                    f"approval_event_{step}_{i}",
                    _publish_approval_event,
                    args=(thread_id, request_id, tc_name, tc_args),
                )

                # Suspend workflow — wait for approval
                approval = await ctx.promise(f"approval-{request_id}").value()

                if approval.get("action") == "deny":
                    await ctx.run(
                        f"persist_deny_{step}_{i}",
                        activities.persist_tool_result,
                        args=(
                            thread_id,
                            tc_name,
                            tc_id,
                            f"Tool '{tc_name}' denied by user",
                            True,
                        ),
                    )
                    continue

            # ── Execute tool (journaled) ─────────────────────────────
            idempotency_key: str | None = None
            if policy.needs_idempotency:
                idempotency_key = str(ctx.uuid())

            tool_result: Dict[str, Any] = await ctx.run(
                f"tool_{step}_{tc_name}_{i}",
                activities.do_tool_exec,
                args=(
                    tc_name,
                    tc_args,
                    thread_id,
                    policy.timeout,
                    idempotency_key,
                ),
            )

            # Persist tool result
            await ctx.run(
                f"persist_tool_{step}_{i}",
                activities.persist_tool_result,
                args=(
                    thread_id,
                    tc_name,
                    tc_id,
                    tool_result.get("content", ""),
                    tool_result.get("is_error", False),
                ),
            )

    # ── Step 3: Completion event ─────────────────────────────────────
    await ctx.run(
        "completion_event",
        _publish_completion,
        args=(thread_id, final_text),
    )

    return {"status": "completed", "final_text": final_text}


# ── Handlers (resolve HITL promises from outside the workflow) ───────────


@agent_workflow.handler()
async def resolve_approval(
    ctx: WorkflowSharedContext,
    payload: Dict[str, Any],
) -> None:
    """Resolve a tool-approval promise.

    Called by the gateway when a user approves/denies a tool call.
    Payload must contain ``request_id`` and ``action`` ("approve"/"deny").
    """
    request_id = payload["request_id"]
    await ctx.promise(f"approval-{request_id}").resolve(payload)


@agent_workflow.handler()
async def resolve_human_input(
    ctx: WorkflowSharedContext,
    payload: Dict[str, Any],
) -> None:
    """Resolve a human-input promise.

    Called when a user responds to an ``ask_human`` tool call.
    Payload must contain ``request_id`` and ``response``.
    """
    request_id = payload["request_id"]
    await ctx.promise(f"human-{request_id}").resolve(payload)


# ── Internal helper functions (called inside ctx.run) ────────────────────


async def _publish_hitl_event(
    thread_id: str,
    request_id: str,
    tool_name: str,
    tool_input: Dict[str, Any],
) -> None:
    """Publish a human_input_request event to NATS."""
    if activities._nats is not None:
        await activities._nats.publish(
            thread_id,
            {
                "type": "human_input_request",
                "request_id": request_id,
                "tool_name": tool_name,
                "prompt": tool_input.get("prompt", ""),
                "options": tool_input.get("options"),
            },
        )


async def _publish_approval_event(
    thread_id: str,
    request_id: str,
    tool_name: str,
    tool_input: Dict[str, Any],
) -> None:
    """Publish a tool_approval_request event to NATS."""
    if activities._nats is not None:
        await activities._nats.publish(
            thread_id,
            {
                "type": "tool_approval_request",
                "request_id": request_id,
                "tool_name": tool_name,
                "input": tool_input,
            },
        )


async def _publish_completion(thread_id: str, final_text: str) -> None:
    """Publish a completion event to NATS."""
    if activities._nats is not None:
        await activities._nats.publish(
            thread_id,
            {
                "type": "completion",
                "message": final_text,
            },
        )
