"""Anthropic Claude model client implementation."""

from __future__ import annotations
from ravi.logger import setup_logging

import json
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from anthropic import AsyncAnthropic

from ravi.kernel.llm import LLMClient
from ravi.kernel import ChatMessage, ContentBlock
from ravi.kernel.content import (
    TextBlock,
    ToolUseBlock,
    DataBlock,
)
from ravi.kernel.stream import TextDelta, ReasoningDelta, CompletionEvent
from ravi.adapters.llm.encoders.anthropic import (
    encode_messages as _encode_messages,
    encode_tools as _encode_tools,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = setup_logging()


class AnthropicClient(LLMClient):
    """Anthropic Claude API client — text and vision.

    Uses the ``anthropic`` SDK (``AsyncAnthropic``) for all operations:
      • ``generate`` / ``generate_stream`` → Messages API
      • ``count_tokens``                   → ``client.messages.count_tokens``

    Audio and image generation are not supported by Claude.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key
        self.client = AsyncAnthropic(api_key=api_key)

    # ── Private helpers — delegates to ``core.messages.encoders.anthropic`` ──

    def _serialize_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Serialise framework messages into (system_prompt, messages).

        Delegates to the centralised Anthropic encoder.
        """
        return _encode_messages(messages)

    def _serialize_tools(
        self, tools: Optional[list[dict[str, Any]]]
    ) -> Optional[list[dict[str, Any]]]:
        """Convert tool schemas to Anthropic format.

        Delegates to the centralised Anthropic encoder.
        """
        return _encode_tools(tools)

    def _build_thinking_param(self, kwargs: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Build the ``thinking`` parameter from kwargs.

        Accepts:
          - ``thinking=True``
          - ``thinking={"type": "enabled", "budget_tokens": N}``
          - ``thinking_budget=N`` (shorthand)
          - ``thinking="adaptive"`` (Claude 4.6+)
        """
        thinking_val = kwargs.pop("thinking", None)
        budget = kwargs.pop("thinking_budget", None)

        if thinking_val is None and budget is None:
            return None

        if isinstance(thinking_val, dict):
            return thinking_val

        if thinking_val == "adaptive" or thinking_val == "auto":
            return {"type": "adaptive"}

        # Default: enabled with budget
        token_budget = budget if budget else 10_000
        return {"type": "enabled", "budget_tokens": token_budget}

    @staticmethod
    def _normalize_tool_choice(
        tool_choice: Optional[str | dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Translate tool forcing into Anthropic Messages API shape."""
        if not tool_choice:
            return None
        if isinstance(tool_choice, str):
            if tool_choice == "auto":
                return {"type": "auto"}
            if tool_choice == "required":
                return {"type": "any"}
            if tool_choice == "none":
                return {"type": "none"}
            return {"type": "tool", "name": tool_choice}
        return tool_choice

    # ── Text / Vision (required) ─────────────────────────────────────────────

    async def generate(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        system_instructions: str = "",
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> list[ContentBlock]:
        """Generate a single response from Anthropic using Messages API."""
        _, conversation = self._serialize_messages(messages)
        system = system_instructions

        thinking_param = self._build_thinking_param(kwargs)

        params: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": conversation,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens or 8192),
        }

        if system:
            # Use structured system block with cache_control for prompt caching
            params["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # Thinking is not compatible with temperature
        if thinking_param:
            params["thinking"] = thinking_param
        else:
            if "temperature" in kwargs:
                params["temperature"] = kwargs["temperature"]
            else:
                params["temperature"] = self.temperature

        anthropic_tools = self._serialize_tools(tools)
        normalized_tool_choice = self._normalize_tool_choice(tool_choice)
        if anthropic_tools:
            params["tools"] = anthropic_tools
            if normalized_tool_choice:
                params["tool_choice"] = normalized_tool_choice

        response = await self.client.messages.create(**params)

        final_blocks: list[ContentBlock] = []

        has_tool_calls = False
        for block in response.content:
            if block.type == "thinking":
                pass  # reasoning blocks not surfaced in non-streaming generate()
            elif block.type == "text":
                final_blocks.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                has_tool_calls = True
                final_blocks.append(
                    ToolUseBlock(
                        call_id=block.id,
                        tool_name=block.name,
                        arguments=(
                            block.input
                            if isinstance(block.input, dict)
                            else json.loads(block.input)
                        ),
                    )
                )

        # Structured output parsing
        if response_format is not None and not has_tool_calls:
            text_blocks = [b.text for b in final_blocks if isinstance(b, TextBlock)]
            final_text = "".join(text_blocks)
            if final_text:
                try:
                    parsed_obj = response_format.model_validate_json(final_text)
                    final_blocks.append(
                        DataBlock(data=parsed_obj.model_dump(mode="json"))
                    )
                except Exception:
                    logger.debug(
                        "Failed to parse structured output from Claude: %s",
                        final_text[:200],
                    )

        return final_blocks

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        system_instructions: str = "",
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        """Generate a streaming response from Anthropic using Messages API."""

        _, conversation = self._serialize_messages(messages)
        system = system_instructions

        thinking_param = self._build_thinking_param(kwargs)

        params: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": conversation,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens or 8192),
        }

        if system:
            # Use structured system block with cache_control for prompt caching
            params["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # Thinking is not compatible with temperature
        if thinking_param:
            params["thinking"] = thinking_param
        else:
            if "temperature" in kwargs:
                params["temperature"] = kwargs["temperature"]
            else:
                params["temperature"] = self.temperature

        anthropic_tools = self._serialize_tools(tools)
        normalized_tool_choice = self._normalize_tool_choice(tool_choice)
        if anthropic_tools:
            params["tools"] = anthropic_tools
            if normalized_tool_choice:
                params["tool_choice"] = normalized_tool_choice

        # Accumulate for final message
        text_parts: list[str] = []
        collected_tool_calls: list[ToolUseBlock] = []
        current_tool_id: Optional[str] = None
        current_tool_name: Optional[str] = None
        current_tool_json: str = ""
        _input_tokens = 0
        _output_tokens = 0
        stop_reason: Optional[str] = None

        async with self.client.messages.stream(**params) as stream:
            async for event in stream:
                event_any: Any = event
                event_type = event_any.type

                if event_type == "message_start":
                    if hasattr(event_any, "message") and hasattr(
                        event_any.message, "usage"
                    ):
                        _input_tokens = getattr(
                            event_any.message.usage, "input_tokens", 0
                        )

                elif event_type == "content_block_start":
                    block = event_any.content_block
                    if block.type == "tool_use":
                        current_tool_id = block.id
                        current_tool_name = block.name
                        current_tool_json = ""

                elif event_type == "content_block_delta":
                    delta = event_any.delta
                    if delta.type == "thinking_delta":
                        thinking_text = getattr(delta, "thinking", "")
                        if thinking_text:
                            yield ReasoningDelta(text=thinking_text)
                    elif delta.type == "text_delta":
                        text_parts.append(delta.text)
                        yield TextDelta(text=delta.text)
                    elif delta.type == "input_json_delta":
                        current_tool_json += delta.partial_json
                    # signature_delta is intentionally ignored (opaque)

                elif event_type == "content_block_stop":
                    if current_tool_id and current_tool_name:
                        try:
                            args = (
                                json.loads(current_tool_json)
                                if current_tool_json
                                else {}
                            )
                        except json.JSONDecodeError:
                            args = {}
                        collected_tool_calls.append(
                            ToolUseBlock(
                                call_id=current_tool_id,
                                tool_name=current_tool_name,
                                arguments=args,
                            )
                        )
                        current_tool_id = None
                        current_tool_name = None
                        current_tool_json = ""

                elif event_type == "message_delta":
                    if hasattr(event_any, "usage"):
                        _output_tokens = getattr(event_any.usage, "output_tokens", 0)
                    stop_reason = getattr(event_any, "delta", None)
                    if stop_reason and hasattr(stop_reason, "stop_reason"):
                        stop_reason = stop_reason.stop_reason  # type: ignore[attr-defined]

                elif event_type == "message_stop":
                    pass  # Handled below

        # Build final message
        final_text = "".join(text_parts) if text_parts else ""
        final_blocks: list[ContentBlock] = []

        if final_text:
            final_blocks.append(TextBlock(text=final_text))

        has_tool_calls = False
        if collected_tool_calls:
            has_tool_calls = True
            final_blocks.extend(collected_tool_calls)

        # Structured output parsing
        if response_format is not None and final_text and not has_tool_calls:
            try:
                parsed_obj = response_format.model_validate_json(final_text)
                final_blocks.append(DataBlock(data=parsed_obj.model_dump(mode="json")))
            except Exception:
                logger.debug(
                    "Stream: failed to parse structured output from Claude: %s",
                    final_text[:200],
                )

        yield CompletionEvent(content=final_blocks)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        """Count tokens using Anthropic's token counting API."""
        _, conversation = self._serialize_messages(messages)
        try:
            result = await self.client.messages.count_tokens(
                model=self.model,
                messages=conversation,  # type: ignore[arg-type]
            )
            return result.input_tokens
        except Exception:
            # Fallback: rough estimate (4 chars ≈ 1 token)
            total_chars = sum(len(json.dumps(msg)) for msg in conversation)
            return total_chars // 4
