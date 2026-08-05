"""ToolChainTool — the LLM-facing tool for sandboxed code-mode chaining.

The LLM writes one Python script that calls multiple tools and pipes results
between them.  The script runs in the existing CodeInterpreter sandbox;
every ``tools.<name>(...)`` call is routed back via the bridge to the
framework-side ``ToolInvoker`` for risk/approval/ctx enforcement.

The tool is a normal ``Tool`` (LOCAL execution) so the existing ReAct dispatch
path is unchanged.  Its risk is SAFE because ToolInvoker enforces per-call
risk inside the chain.

Wiring (lifespan)::

    tool = ToolChainTool(
        invoker=ToolInvoker(registry, approval, artifact_store, policy),
        interpreter=code_interpreter_tool,  # CodeInterpreterTool instance
        bridge_registry=app.state.chain_bridge,
    )
    toolbox.add(tool)

When ``interpreter`` is None (CodeInterpreter not deployed), the constructor
raises ``RuntimeError`` — the tool is simply not registered; the model falls
back to normal sequential tool calls.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from substrate.agents.tools.invoker import InvokerSession, ToolInvoker
from substrate.capabilities.tools.chain.bridge import BridgeSession, ChainBridgeRegistry
from substrate.capabilities.tools.chain.prelude import build_prelude
from substrate.kernel.tools.chain import ChainRunResult
from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.kernel.tools import ToolExecutionResult, ToolRisk
from substrate.logger import setup_logging

logger = setup_logging("substrate.capabilities.tools.chain.tool")


class ToolChainTool:
    """Execute a Python script that calls multiple tools in one sandbox run.

    Available in the sandbox::

        # Call any registered tool
        result = tools.query_db(query="SELECT * FROM events LIMIT 1000")
        # result.text / result.structured / result.ref / result.files

        # Pass large data by ref — payload never re-enters the sandbox
        summary = tools.analysis_tool(data=result)

        # Materialise to disk for pandas / numpy compute
        path = await result.materialize()  # -> "/workspace/artifact_abc123.csv"

        # Return media produced in the sandbox
        chart_ref = artifacts.put("/workspace/chart.png", "image/png")
        return {"summary": summary.text, "chart": chart_ref}

    The tool_chain tool itself is SAFE; individual tool risks are enforced by
    ToolInvoker inside the chain (HIGH/CRITICAL tools still require approval —
    but with a bounded timeout so the sandbox never hangs).
    """

    name: str = "tool_chain"
    risk: ToolRisk = ToolRisk.SAFE
    description: str = (
        "Execute a Python script that chains multiple tools together in one "
        "sandbox run.  Use 'tools.<name>(...)' to call any registered tool; "
        "results are ToolResult handles — use .text, .structured, .files, or "
        "await .materialize() (streams large data to a local file path). "
        "Pipe outputs between tools by passing a ToolResult as an argument — "
        "the payload flows store→tool directly and never re-enters the sandbox. "
        "Upload sandbox-produced files via artifacts.put(path). "
        "Return a dict or string from the script body. "
        "Example: "
        "data = tools.query_db(query='SELECT * FROM logs'); "
        "result = tools.analysis_tool(data=data); "
        "return {'summary': result.text}"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python script body.  Available: tools.<name>(**kwargs), "
                    "ToolResult handles, artifacts.put(path), "
                    "await result.materialize(). Return a value to include it "
                    "in the chain output."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Max execution time in seconds (default: 300).",
                "default": 300,
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        invoker: ToolInvoker,
        interpreter: Any,
        bridge_registry: ChainBridgeRegistry | None = None,
        bridge_base_url: str = "",
    ) -> None:
        if interpreter is None:
            raise RuntimeError(
                "ToolChainTool requires a CodeInterpreterTool instance — the "
                "sandbox runtime failed to initialize (see SANDBOX_RUNTIME)."
            )
        self._invoker = invoker
        self._interpreter = interpreter
        self._bridge_registry = bridge_registry or ChainBridgeRegistry()
        self._bridge_base_url = bridge_base_url

    async def execute(
        self,
        *,
        code: str,
        timeout: int = 300,
        ctx: Any | None = None,
    ) -> ToolExecutionResult:
        chain_id = str(uuid.uuid4())
        start_ms = int(time.monotonic() * 1000)

        async with self._invoker.open_session() as inv_session:
            bridge_session = BridgeSession(
                chain_id=chain_id,
                invoker=self._invoker,
                invoker_session=inv_session,
                ctx=ctx,
            )
            self._bridge_registry.register(bridge_session)

            try:
                return await self._run_chain(
                    code=code,
                    timeout=timeout,
                    ctx=ctx,
                    chain_id=chain_id,
                    bridge_session=bridge_session,
                    inv_session=inv_session,
                    start_ms=start_ms,
                )
            finally:
                self._bridge_registry.deregister(chain_id)

    async def _run_chain(
        self,
        *,
        code: str,
        timeout: int,
        ctx: Any | None,
        chain_id: str,
        bridge_session: BridgeSession,
        inv_session: InvokerSession,
        start_ms: int,
    ) -> ToolExecutionResult:
        policy = self._invoker.policy
        effective_timeout = min(timeout, int(policy.total_timeout_s))

        # Set the session_id on the interpreter so it reuses the agent's VM
        if ctx is not None and hasattr(ctx, "session_id"):
            self._interpreter.session_id = ctx.session_id

        # Build prelude
        tool_names = list(self._invoker._registry.names())
        prelude = build_prelude(
            tool_names=tool_names,
            bridge_url=self._bridge_base_url,
            chain_id=chain_id,
            chain_token=bridge_session.token,
        )

        # Wrap user code in async function, inject prelude
        wrapped_code = _wrap_user_code(code)
        full_code = prelude + "\n" + wrapped_code

        logger.info(
            "chain[%s]: running %d-line script via code_interpreter",
            chain_id,
            code.count("\n") + 1,
        )

        # Execute in sandbox
        try:
            exec_result = await self._interpreter.execute(
                code=full_code,
                exec_type="python",
                timeout=effective_timeout,
            )
        except Exception as exc:
            logger.exception("chain[%s]: interpreter error", chain_id)
            return _error_result(
                f"Sandbox error: {type(exc).__name__}: {exc}",
                trace=inv_session.trace,
                duration_ms=int(time.monotonic() * 1000) - start_ms,
            )

        duration_ms = int(time.monotonic() * 1000) - start_ms
        trace = inv_session.trace

        if exec_result.is_error:
            error_text = exec_result.text
            logger.warning(
                "chain[%s]: script error after %dms: %s",
                chain_id,
                duration_ms,
                error_text[:200],
            )
            return _error_result(error_text, trace=trace, duration_ms=duration_ms)

        # Reconstruct any media artifacts in the output
        output_content: list[Any] = []
        output_text = exec_result.text or ""

        # Check for media blocks returned from code interpreter
        media = getattr(exec_result, "media", None) or []
        for block in media:
            if isinstance(block, ImageBlock):
                output_content.append(block)

        chain_result = ChainRunResult(
            status="ok",
            output_text=output_text,
            logs="",
            tool_calls=inv_session.call_count,
            duration_ms=duration_ms,
            call_trace=trace,
        )

        # Build final ToolExecutionResult
        summary = _build_summary(output_text, trace, duration_ms)
        content: list[Any] = [TextBlock(text=summary)]
        content.extend(output_content)

        return ToolExecutionResult(
            content=content,
            is_error=False,
            structured_content=chain_result.model_dump(),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_user_code(code: str) -> str:
    """Wrap user code in an async function so await works at top level."""
    indented = "\n".join("    " + line for line in code.splitlines())
    return (
        "import asyncio as _asyncio\n\n"
        "async def __chain__():\n"
        f"{indented}\n\n"
        "__chain_result__ = _asyncio.get_event_loop().run_until_complete(__chain__())\n"
        "print('__chain_return__:', __chain_result__)\n"
    )


def _build_summary(
    output_text: str,
    trace: list[Any],
    duration_ms: int,
) -> str:
    parts = []
    if output_text:
        parts.append(output_text)
    if trace:
        parts.append(f"\n[chain: {len(trace)} tool calls in {duration_ms}ms]")
    return "\n".join(parts)


def _error_result(
    error_text: str,
    *,
    trace: list[Any],
    duration_ms: int,
) -> ToolExecutionResult:
    chain_result = ChainRunResult(
        status="error",
        error=error_text[:2000],
        tool_calls=len(trace),
        duration_ms=duration_ms,
        call_trace=trace,
    )
    return ToolExecutionResult(
        content=[TextBlock(text=f"Chain error: {error_text[:500]}")],
        is_error=True,
        structured_content=chain_result.model_dump(),
    )


__all__ = ["ToolChainTool"]
