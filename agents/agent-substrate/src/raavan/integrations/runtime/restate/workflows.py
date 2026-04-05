"""Restate workflow definitions — durable pipeline, chain, and agent workflows.

Replaces both:
- ``catalog/_temporal/workflows.py`` (PipelineWorkflow, ChainWorkflow)
- ``distributed/workflow.py`` (AgentWorkflow — durable ReAct loop)

Each workflow is a Restate ``Workflow`` virtual object whose ``run``
handler is the durable main entrypoint.  Activities executed inside
``ctx.run("name", fn, args=(...))`` are journaled and replay-safe.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from restate import Workflow, WorkflowContext, WorkflowSharedContext

from raavan.integrations.runtime.restate import activities
from raavan.integrations.runtime.restate.policies import get_policy

logger = logging.getLogger(__name__)


# ── Pipeline Workflow ────────────────────────────────────────────────────

pipeline_workflow = Workflow("PipelineWorkflow")


@pipeline_workflow.main()
async def pipeline_run(
    ctx: WorkflowContext, definition: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute all pipeline steps sequentially as durable activities."""
    steps: List[Dict[str, Any]] = definition.get("steps", [])
    name: str = definition.get("name", "unnamed")
    results: List[Dict[str, Any]] = []
    prev_result: Dict[str, Any] | None = None

    logger.info("PipelineWorkflow '%s' started with %d steps", name, len(steps))

    for i, step in enumerate(steps):
        inputs = _resolve_refs(step.get("input_mapping", {}), prev_result, results)
        step_input = {
            "adapter_name": step["adapter_name"],
            "action": step.get("action", "execute"),
            "inputs": inputs,
        }

        result: Dict[str, Any] = await ctx.run(
            f"step_{i}_{step['adapter_name']}",
            activities.execute_adapter_step,
            args=(step_input,),
        )

        results.append(result)
        prev_result = result

        if not result.get("success", False):
            logger.error(
                "Step %d (%s) failed: %s", i, step["adapter_name"], result.get("error")
            )
            return {
                "pipeline": name,
                "success": False,
                "completed_steps": i,
                "total_steps": len(steps),
                "error": result.get("error"),
                "step_results": results,
            }

    logger.info("PipelineWorkflow '%s' completed all %d steps", name, len(steps))
    return {
        "pipeline": name,
        "success": True,
        "completed_steps": len(steps),
        "total_steps": len(steps),
        "step_results": results,
    }


# ── Chain Workflow ───────────────────────────────────────────────────────

chain_workflow = Workflow("ChainWorkflow")


@chain_workflow.main()
async def chain_run(ctx: WorkflowContext, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a code-based adapter chain as a durable activity."""
    code: str = params["code"]
    timeout: int = params.get("timeout", 120)

    logger.info("ChainWorkflow started (timeout=%ds)", timeout)

    result: Dict[str, Any] = await ctx.run(
        "execute_chain",
        activities.execute_code_chain,
        args=({"code": code, "timeout": timeout},),
    )
    return result


# ── Agent Workflow (durable ReAct loop) ──────────────────────────────────

agent_workflow = Workflow("AgentWorkflow")


@agent_workflow.main()
async def agent_run(ctx: WorkflowContext, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Durable ReAct agent loop with HITL suspension via promises.

    Payload keys:
        thread_id, user_content, system_instructions, model, max_iterations
    """
    thread_id: str = payload["thread_id"]
    user_content: str = payload["user_content"]
    system_instructions: str = payload.get(
        "system_instructions", "You are a helpful agent."
    )
    model: str = payload.get("model", "gpt-4o-mini")
    max_iterations: int = payload.get("max_iterations", 30)

    # Step 0: Restore memory
    await ctx.run("restore_memory", activities.restore_memory, args=(thread_id,))

    # Step 1: Persist user message
    await ctx.run(
        "persist_user",
        activities.persist_message,
        args=(thread_id, "user", user_content),
    )

    # Step 2: ReAct loop
    tool_schemas = activities.get_tool_schemas()
    final_text: str = ""

    for step in range(max_iterations):
        # THINK: Call LLM
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

        # Persist assistant text (if any)
        assistant_text = llm_result.get("content") or ""
        if assistant_text:
            await ctx.run(
                f"persist_assistant_{step}",
                activities.persist_message,
                args=(thread_id, "assistant", assistant_text),
            )

        # ACT: Execute each tool call
        for i, tc in enumerate(tool_calls):
            tc_name: str = tc["name"]
            tc_args: Dict[str, Any] = tc["arguments"]
            tc_id: str = tc["call_id"]
            policy = get_policy(tc_name)

            # HITL: human input request
            if policy.is_hitl_input:
                request_id = str(ctx.rand.uuid4())
                await ctx.run(
                    f"hitl_event_{step}_{i}",
                    activities.publish_event,
                    args=(
                        thread_id,
                        {
                            "type": "human_input_request",
                            "request_id": request_id,
                            "tool_name": tc_name,
                            "prompt": tc_args.get("prompt", ""),
                            "options": tc_args.get("options"),
                        },
                    ),
                )
                human_response = await ctx.promise(f"human-{request_id}").value()
                answer = human_response.get("response", "")
                await ctx.run(
                    f"persist_human_{step}_{i}",
                    activities.persist_tool_result,
                    args=(thread_id, tc_name, tc_id, answer, False),
                )
                continue

            # HITL: tool approval gate
            if policy.requires_approval:
                request_id = str(ctx.rand.uuid4())
                await ctx.run(
                    f"approval_event_{step}_{i}",
                    activities.publish_event,
                    args=(
                        thread_id,
                        {
                            "type": "tool_approval_request",
                            "request_id": request_id,
                            "tool_name": tc_name,
                            "input": tc_args,
                        },
                    ),
                )
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

            # Execute tool (journaled)
            idempotency_key: str | None = None
            if policy.needs_idempotency:
                idempotency_key = str(ctx.rand.uuid4())

            tool_result: Dict[str, Any] = await ctx.run(
                f"tool_{step}_{tc_name}_{i}",
                activities.do_tool_exec,
                args=(tc_name, tc_args, thread_id, policy.timeout, idempotency_key),
            )

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

    # Step 3: Completion event
    await ctx.run(
        "completion_event",
        activities.publish_event,
        args=(thread_id, {"type": "completion", "message": final_text}),
    )

    return {"status": "completed", "final_text": final_text}


# ── Shared handlers — resolve HITL promises from outside ─────────────────


@agent_workflow.handler()
async def resolve_approval(ctx: WorkflowSharedContext, payload: Dict[str, Any]) -> None:
    """Resolve a tool-approval promise.

    Payload must contain ``request_id`` and ``action`` ("approve"/"deny").
    """
    request_id = payload["request_id"]
    await ctx.promise(f"approval-{request_id}").resolve(payload)


@agent_workflow.handler()
async def resolve_human_input(
    ctx: WorkflowSharedContext, payload: Dict[str, Any]
) -> None:
    """Resolve a human-input promise.

    Payload must contain ``request_id`` and ``response``.
    """
    request_id = payload["request_id"]
    await ctx.promise(f"human-{request_id}").resolve(payload)


# ── Helpers ──────────────────────────────────────────────────────────────


def _resolve_refs(
    mapping: Dict[str, Any],
    prev: Dict[str, Any] | None,
    all_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Replace ``$prev.field`` and ``$step[n].field`` references."""
    resolved: Dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, str) and value.startswith("$prev.") and prev is not None:
            field = value[len("$prev.") :]
            resolved[key] = prev.get(field, value)
        elif isinstance(value, str) and value.startswith("$step["):
            try:
                bracket_end = value.index("]")
                idx = int(value[6:bracket_end])
                field = value[bracket_end + 2 :]
                resolved[key] = all_results[idx].get(field, value)
            except (ValueError, IndexError):
                resolved[key] = value
        else:
            resolved[key] = value
    return resolved
