"""LLM-powered Assistant Agent — Layer 2 Actor Agent.

Extends ``RuntimeAgent`` to provide a fully autonomous, LLM-powered assistant
capable of running in the actor runtime. It implements:
- Input guardrails checking
- Memory/Context management
- ReAct tool calling execution loop with console-style output
- Output guardrails checking

It retains complete compatibility with standard model clients, contexts,
and memories while offering a clean, actor-native async interface.
"""

from __future__ import annotations

import asyncio
import logging

import time
from typing import Optional
from uuid import uuid4


from ravi.core.messages.content import ContentBlock
from ravi.core.runtime._identity import TopicId
from ravi.core.runtime._contracts import MessageContext
from ravi.core.runtime._protocol import AgentRuntime
from ravi.core.agents.runtime_agent import RuntimeAgent
from ravi.core.agents._tool_execution import (
    ParsedToolCall,
    parse_tool_call,
)
from ravi.core.context.base_context import ModelContext
from ravi.core.llm.base_client import BaseModelClient
from ravi.core.memory.base_memory import BaseMemory
from ravi.core.memory.unbounded_memory import UnboundedMemory
from ravi.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from ravi.core.middleware.base import BaseMiddleware, MiddlewareContext, MiddlewareStage
from ravi.core.middleware.runner import MiddlewarePipeline
from ravi.exceptions import GuardrailTripwireError
from ravi.core.guardrails.base_guardrail import GuardrailType

from ravi.core.tools.base_tool import BaseTool
from ravi.core.catalog import AgentCatalogRegistry

logger = logging.getLogger("ravi.core.agents.runtime_assistant_agent")


from rich.console import Console

_rich_console = Console()


def _console_print(msg: str, *, flush: bool = True) -> None:
    """Print to stdout with flush for notebook/terminal streaming."""
    _rich_console.print(msg)


def _message_received_banner(agent_name: str, sender_name: str, text: str) -> None:
    # Silent in interactive mode to avoid echoing what the user just typed
    pass


def _step_header(agent_name: str, iteration: int) -> None:
    # Silent! No developer iteration headers
    pass


def _thought_banner(agent_name: str, thought: str) -> None:
    # Silent! Hide intermediate raw LLM thought monologues
    pass


def _tool_call_banner(agent_name: str, tool_name: str, args: dict) -> None:
    args_str = ", ".join(f"[bold yellow]{k}[/bold yellow]={v!r}" for k, v in args.items())
    _rich_console.print(f"  [yellow]⚙️  Running tool:[/yellow] [bold cyan]{tool_name}[/bold cyan]({args_str})...", end="")


def _tool_result_banner(agent_name: str, tool_name: str, result: str, elapsed: float) -> None:
    result_preview = result.strip().replace("\n", " ")
    if len(result_preview) > 80:
        result_preview = result_preview[:80] + "..."
    # Conclude the tool call printed in _tool_call_banner on the same line!
    _rich_console.print(f" [green]Done![/green] [dim]({elapsed:.2f}s)[/dim] → [italic green]{result_preview}[/italic green]")


def _final_answer_banner(agent_name: str, text: str) -> None:
    _rich_console.print(f"\n[bold magenta]🤖 {agent_name} ›[/bold magenta] {text.strip()}\n")


class RuntimeAssistantAgent(RuntimeAgent):
    """An LLM-powered cognitive agent running on the Layer 2 actor runtime.

    Implements a robust ReAct (Reasoning + Acting) loop with guardrails and
    decoupled tool execution.

    Parameters
    ----------
    name:
        Agent type name (registered with the runtime).
    runtime:
        The ``AgentRuntime`` to register with.
    model_client:
        The LLM provider client (e.g. OpenAIClient, GeminiClient).
    model_context:
        The model context constructor (e.g. SlidingWindowContext).
    system_instructions:
        System instructions for the LLM.
    key:
        Instance key for the AgentId. Defaults to ``"default"``.
    tools:
        List of tools this agent can call.
    memory:
        Custom memory backend. Defaults to ``UnboundedMemory``.
    input_guardrails:
        List of guardrails applied to the incoming user message.
    output_guardrails:
        List of guardrails applied to the outgoing assistant response.
    max_iterations:
        Maximum number of thought-action iterations allowed per run.
    subscriptions:
        List of topics to subscribe to on start.
    verbose:
        When True, prints console-style output showing agent reasoning,
        tool calls, and results.  Defaults to True.
    """

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        model_client: BaseModelClient,
        model_context: ModelContext,
        system_instructions: str = "You are a helpful assistant.",
        key: str = "default",
        tools: Optional[list[BaseTool]] = None,
        memory: Optional[BaseMemory] = None,
        max_iterations: int = 15,
        subscriptions: Optional[list[TopicId]] = None,
        verbose: bool = True,
        middleware: Optional[list[BaseMiddleware]] = None,
        catalog: Optional[AgentCatalogRegistry] = None,
    ) -> None:
        super().__init__(
            name=name,
            runtime=runtime,
            key=key,
            description=f"LLM Assistant Agent: {name}",
            tools=tools,
            subscriptions=subscriptions,
            catalog=catalog,
        )
        self.model_client = model_client
        self.model_context = model_context
        self.system_instructions = system_instructions
        self.memory = memory or UnboundedMemory()
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.middleware_pipeline = MiddlewarePipeline(middleware)

    # -- Message Handling (Core Cognitive Loop) ------------------------------

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        """Handle incoming messages with the ReAct LLM + tool loop."""
        input_text = content[0].text if content else ""
        run_id = str(uuid4())
        sender_name = ctx.sender.type if ctx.sender else "user"

        if self.verbose:
            _message_received_banner(self.name, sender_name, input_text)

        # Ensure system prompt is seeded into memory
        if await self.memory.size() == 0:
            await self.memory.add_message(SystemMessage(content=self.system_instructions))

        # Append incoming message to memory
        user_msg = UserMessage(content=[input_text])
        await self.memory.add_message(user_msg)

        # Build tool schemas for LLM call
        tool_schemas = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in self.tools
        ]

        # ── 2. ReAct LOOP (THINK & ACT) ─────────────────────────────────────
        for iteration in range(1, self.max_iterations + 1):
            if self.verbose:
                _step_header(self.name, iteration)

            # Build current messages for LLM context
            memory_messages = await self.memory.get_messages()
            messages = await self.model_context.build(
                session_id=self.name,
                current_input=input_text,
                raw_messages=memory_messages,
                model_client=self.model_client,
            )

            # Generate thought/completion using the middleware pipeline if present
            async def _do_generate(ctx: MiddlewareContext) -> AssistantMessage:
                return await self.model_client.generate(
                    messages=ctx.metadata.get("messages", messages),
                    tools=tool_schemas or None,
                    tool_choice="auto" if tool_schemas else None,
                )

            if self.middleware_pipeline.middleware:
                mw_ctx = MiddlewareContext(
                    stage=MiddlewareStage.LLM_CALL,
                    agent_name=self.name,
                    run_id=run_id,
                    correlation_id=run_id,
                    input_text=input_text,
                    metadata={"messages": list(messages)},
                )
                try:
                    response = await self.middleware_pipeline.run(mw_ctx, _do_generate)
                except Exception as e:
                    if self.verbose:
                        _console_print(f"  ❌ [{self.name}] Guardrail blocked/tripped: {e}")
                    if isinstance(e, GuardrailTripwireError):
                        tripped_res = e.details.get("result", {})
                        g_type = tripped_res.get("guardrail_type")
                        if g_type == GuardrailType.INPUT:
                            return f"Request blocked by guardrails: {e.message}"
                        else:
                            return f"Response blocked by guardrails: {e.message}"
                    err_str = str(e)
                    if "Response" in err_str or "Output" in err_str:
                        return f"Response blocked by guardrails: {e}"
                    return f"Request blocked by guardrails: {e}"
            else:
                response = await self.model_client.generate(
                    messages=messages,
                    tools=tool_schemas or None,
                    tool_choice="auto" if tool_schemas else None,
                )

            # Save the response message to memory
            await self.memory.add_message(response)

            # Extract thought text and print it
            thought_text = ""
            if response.content:
                for part in response.content:
                    if isinstance(part, str):
                        thought_text += part
                    elif isinstance(part, dict) and "text" in part:
                        thought_text += part["text"]

            if self.verbose and thought_text.strip():
                _thought_banner(self.name, thought_text)

            # If no tool calls were requested, this is the final answer!
            if not response.tool_calls:
                final_text = ""
                if response.content:
                    for part in response.content:
                        if isinstance(part, str):
                            final_text += part
                        elif isinstance(part, dict) and "text" in part:
                            final_text += part["text"]

                if self.verbose:
                    _final_answer_banner(self.name, final_text)

                return final_text

            # Execute requested tools
            tool_tasks = []
            for tc_raw in response.tool_calls:
                parsed = parse_tool_call(tc_raw)
                tool = self.get_tool(parsed.name)
                if self.verbose:
                    _tool_call_banner(self.name, parsed.name, parsed.arguments)
                tool_tasks.append(self._execute_tool(parsed, tool))

            # Wait for all tool executions to finish
            results = await asyncio.gather(*tool_tasks)

            # Add tool execution results back to memory
            for result_msg in results:
                await self.memory.add_message(result_msg)

        if self.verbose:
            _console_print(f"  ⚠️  [{self.name}] Max iterations ({self.max_iterations}) reached!")
        return "Max iterations reached without a final response."

    # -- Tool execution helper -----------------------------------------------

    async def _execute_tool(
        self, parsed: ParsedToolCall, tool: Optional[BaseTool]
    ) -> ToolExecutionResultMessage:
        """Helper to run a single tool and return a ToolExecutionResultMessage."""
        t0 = time.monotonic()

        if tool is None:
            err_msg = f"Tool '{parsed.name}' not found."
            logger.error("[%s] %s", self.name, err_msg)
            if self.verbose:
                _rich_console.print(f"  [bold red]❌ [{self.name}] {err_msg}[/bold red]")
            return ToolExecutionResultMessage(
                tool_call_id=parsed.call_id,
                name=parsed.name,
                content=err_msg,
            )

        try:
            # Re-entrant locking protection: check if tool uses resource locking
            lock_handle = None
            if self.runtime and hasattr(self.runtime, "resource_locks") and getattr(tool, "resource_uri", None):
                lock_handle = await self.runtime.resource_locks.acquire(
                    resource_uri=tool.resource_uri,
                    agent_id=self.id,
                )

            # Call the tool
            result = await tool.execute(**parsed.arguments)

            # Release lock if acquired
            if lock_handle and self.runtime and hasattr(self.runtime, "resource_locks"):
                await self.runtime.resource_locks.release(lock_handle)

            elapsed = time.monotonic() - t0
            
            # Extract plain text content for console banner
            banner_text = ""
            if hasattr(result, "content") and isinstance(result.content, list):
                from ravi.core.messages.content import TextBlock
                banner_text = "\n".join(b.text for b in result.content if isinstance(b, TextBlock))
            else:
                banner_text = str(result)

            if self.verbose:
                _tool_result_banner(self.name, parsed.name, banner_text, elapsed)

            return ToolExecutionResultMessage(
                tool_call_id=parsed.call_id,
                name=parsed.name,
                content=result.content if hasattr(result, "content") else banner_text,
                media=getattr(result, "media", None),
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            err_msg = f"Error executing tool '{parsed.name}': {e}"
            logger.exception("[%s] %s", self.name, err_msg)
            if self.verbose:
                _rich_console.print(f" [bold red]FAILED![/bold red] [dim]({elapsed:.2f}s)[/dim] → [red]{e}[/red]")
            return ToolExecutionResultMessage(
                tool_call_id=parsed.call_id,
                name=parsed.name,
                content=err_msg,
            )
