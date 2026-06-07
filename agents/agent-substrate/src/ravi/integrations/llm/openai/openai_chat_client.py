"""OpenAI-compatible Chat Completions client for Groq, OpenRouter, etc.

These providers expose the standard ``/v1/chat/completions`` endpoint
but do **not** support the newer OpenAI Responses API used by
``OpenAIClient``.  This client inherits the shared ``AsyncOpenAI``
setup and audio helpers but overrides ``generate`` and ``generate_stream``
to use the Chat Completions API.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import json
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from ravi.kernel import ChatMessage, ContentBlock
from ravi.kernel.llm import LLMResponse, Usage
from ravi.kernel.content import (
    TextBlock,
    ImageBlock,
    DataBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from ravi.kernel.stream import TextDelta, CompletionEvent
from ravi.integrations.llm.encoders.openai import ensure_strict_tool_schema
from ravi.integrations.llm.openai.openai_client import OpenAIClient

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = setup_logging()


class OpenAIChatCompletionClient(OpenAIClient):
    """LLM client that uses the standard Chat Completions API.

    Drop-in replacement for ``OpenAIClient`` when the provider (Groq,
    OpenRouter, or any OpenAI-compatible server) does not support the
    Responses API.
    """

    # ------------------------------------------------------------------
    # Message / tool serialisation for Chat Completions
    # ------------------------------------------------------------------

    @staticmethod
    def _format_provider_error(exc: Exception) -> str:
        """Return a detailed provider error string for OpenAI-compatible SDK failures."""
        parts: list[str] = [str(exc)]

        body = getattr(exc, "body", None)
        if body:
            try:
                parts.append(
                    f"body={json.dumps(body, ensure_ascii=True, sort_keys=True)}"
                )
            except TypeError:
                parts.append(f"body={body}")

        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            if status_code:
                parts.append(f"status={status_code}")

            text = getattr(response, "text", None)
            if text:
                parts.append(f"response={text}")

        return " | ".join(part for part in parts if part)

    @staticmethod
    def _try_recover_tool_use_failed(
        exc: Exception,
    ) -> Optional[tuple[dict[int, dict[str, Any]], str]]:
        """Extract tool calls from a ``tool_use_failed`` rejection.

        Groq sometimes rejects tool calls server-side but includes the
        generated text in ``body.failed_generation``.  Parse it and
        return ``(collected_tool_calls_dict, finish_reason)`` so the
        caller can continue normally.  Returns ``None`` when recovery
        is not possible.
        """
        body = getattr(exc, "body", None)
        if not isinstance(body, dict):
            return None
        if body.get("code") != "tool_use_failed":
            return None

        raw: str = body.get("failed_generation", "")
        if not raw:
            return None

        import re

        # Groq emits: <function=tool_name{"key": "val"}</function>
        pattern = re.compile(
            r"<function=(\w+)\s*(\{.*?\})\s*(?:</function>|/>)", re.DOTALL
        )
        calls: dict[int, dict[str, Any]] = {}
        for idx, m in enumerate(pattern.finditer(raw)):
            name = m.group(1)
            try:
                arguments = json.loads(m.group(2))
            except json.JSONDecodeError:
                return None
            calls[idx] = {
                "id": f"recovered_{idx}",
                "name": name,
                "arguments": json.dumps(arguments)
                if isinstance(arguments, dict)
                else str(arguments),
            }

        if not calls:
            return None

        logger.info(
            "Recovered %d tool call(s) from tool_use_failed: %s",
            len(calls),
            [c["name"] for c in calls.values()],
        )
        return calls, "tool_calls"

    @staticmethod
    def _serialize_messages_chat(
        messages: list[ChatMessage],
    ) -> list[dict[str, Any]]:
        """Convert framework messages to Chat Completions ``messages`` list."""
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                content = "".join(
                    b.text for b in msg.content if isinstance(b, TextBlock)
                )
                result.append({"role": "system", "content": content})

            elif msg.role == "user":
                parts: list[Any] = []
                for item in msg.content:
                    if isinstance(item, TextBlock):
                        parts.append({"type": "text", "text": item.text})
                    elif isinstance(item, ImageBlock):
                        url = item.url
                        if not url and item.data:
                            import base64 as _b64

                            b64 = _b64.b64encode(item.data).decode()
                            url = f"data:{item.media_type};base64,{b64}"
                        if url:
                            parts.append(
                                {"type": "image_url", "image_url": {"url": url}}
                            )
                if len(parts) == 1 and parts[0].get("type") == "text":
                    result.append({"role": "user", "content": parts[0]["text"]})
                else:
                    result.append({"role": "user", "content": parts})

            elif msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant"}
                text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                if text_parts:
                    entry["content"] = "".join(text_parts)
                else:
                    entry["content"] = None

                tool_calls = [b for b in msg.content if isinstance(b, ToolUseBlock)]
                if tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.call_id,
                            "type": "function",
                            "function": {
                                "name": tc.tool_name,
                                "arguments": (
                                    json.dumps(tc.arguments)
                                    if isinstance(tc.arguments, dict)
                                    else tc.arguments
                                ),
                            },
                        }
                        for tc in tool_calls
                    ]
                result.append(entry)

            elif msg.role == "tool":
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        content_str = ""
                        if isinstance(block.content, list):
                            parts = []
                            for b in block.content:
                                if isinstance(b, dict):
                                    if b.get("type") == "text" and "text" in b:
                                        parts.append(b["text"])
                                elif hasattr(b, "text"):
                                    parts.append(getattr(b, "text"))
                                elif isinstance(b, TextBlock):
                                    parts.append(b.text)
                            content_str = "\n".join(parts)
                        elif isinstance(block.content, str):
                            content_str = block.content
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.call_id,
                                "content": content_str,
                            }
                        )

    def _serialize_tools_chat(
        self,
        tools: Optional[list[dict[str, Any]]],
    ) -> Optional[list[dict[str, Any]]]:
        """Normalise tool schemas to Chat Completions nested format.

        For OpenAI, ``strict: true`` and ``ensure_strict_tool_schema`` are
        applied.  For other providers (Groq, OpenRouter) these are omitted
        because they don't support strict mode and fall back to textual
        tool calls which then fail validation.
        """
        if not tools:
            return None

        provider: str = getattr(self, "provider", "openai")
        use_strict = provider == "openai"

        result: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                fn = dict(tool["function"])
                if "parameters" in fn:
                    if use_strict:
                        fn["parameters"] = ensure_strict_tool_schema(fn["parameters"])
                    else:
                        fn.pop("strict", None)
                if use_strict:
                    fn["strict"] = True
                else:
                    fn.pop("strict", None)
                result.append({"type": "function", "function": fn})
            elif "name" in tool:
                params = (
                    tool.get("parameters")
                    or tool.get("inputSchema")
                    or {"type": "object", "properties": {}}
                )
                fn_dict: dict[str, Any] = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": (
                        ensure_strict_tool_schema(params) if use_strict else params
                    ),
                }
                if use_strict:
                    fn_dict["strict"] = True
                result.append({"type": "function", "function": fn_dict})
            else:
                result.append(tool)
        return result

    @staticmethod
    def _normalize_chat_tool_choice(
        tool_choice: Optional[str | dict[str, Any]],
    ) -> Optional[str | dict[str, Any]]:
        """Translate named-tool forcing into the Chat Completions shape."""
        if not tool_choice:
            return None
        if isinstance(tool_choice, str):
            if tool_choice in {"auto", "required", "none"}:
                return tool_choice
            return {"type": "function", "function": {"name": tool_choice}}
        return tool_choice

    # ------------------------------------------------------------------
    # generate (non-streaming)
    # ------------------------------------------------------------------

    async def generate(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        system_instructions: str = "",
        response_format: Optional[type["BaseModel"]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a response via Chat Completions API."""
        chat_messages = self._serialize_messages_chat(messages)
        if system_instructions:
            chat_messages.insert(0, {"role": "system", "content": system_instructions})
        params: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
        }
        
        with open("/tmp/openai_prompt.json", "w") as f:
            json.dump(params, f, indent=2)
        if "temperature" in kwargs:
            params["temperature"] = kwargs.pop("temperature")
        else:
            params["temperature"] = self.temperature
        if self.max_tokens:
            params["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)

        chat_tools = self._serialize_tools_chat(tools)
        normalized_tool_choice = self._normalize_chat_tool_choice(tool_choice)
        if chat_tools:
            params["tools"] = chat_tools
            if normalized_tool_choice:
                params["tool_choice"] = normalized_tool_choice

        if response_format is not None and not chat_tools:
            params["response_format"] = {"type": "json_object"}

        try:
            response = await self.client.chat.completions.create(**params)
        except Exception as exc:
            # Attempt recovery from tool_use_failed (Groq textual fallback)
            recovered = self._try_recover_tool_use_failed(exc)
            if recovered is not None:
                tc_dict, _ = recovered
                final_blocks: list[ContentBlock] = []
                for _, tc_data in sorted(tc_dict.items()):
                    final_blocks.append(
                        ToolUseBlock(
                            call_id=tc_data["id"],
                            tool_name=tc_data["name"],
                            arguments=(
                                json.loads(tc_data["arguments"])
                                if tc_data["arguments"]
                                else {}
                            ),
                        )
                    )
                return LLMResponse(content=final_blocks, usage=Usage())
            detail = self._format_provider_error(exc)
            logger.exception("Chat completions request failed: %s", detail)
            raise RuntimeError(detail) from exc
        choice = response.choices[0]
        msg = choice.message

        final_blocks: list[ContentBlock] = []
        if msg.content:
            final_blocks.append(TextBlock(text=msg.content))

        has_tool_calls = False
        if msg.tool_calls:
            for tc in msg.tool_calls:
                has_tool_calls = True
                final_blocks.append(
                    ToolUseBlock(
                        call_id=tc.id or "",
                        tool_name=tc.function.name,
                        arguments=(
                            json.loads(tc.function.arguments)
                            if isinstance(tc.function.arguments, str)
                            else tc.function.arguments
                        ),
                    )
                )

        if response_format is not None and msg.content and not has_tool_calls:
            try:
                parsed_obj = response_format.model_validate_json(msg.content)
                final_blocks.append(DataBlock(data=parsed_obj.model_dump(mode="json")))
            except Exception:
                logger.debug(
                    "Chat completions: failed to parse structured output: %s",
                    msg.content[:200],
                )

        u = getattr(response, "usage", None)
        usage = Usage()
        if u:
            cached = 0
            details = getattr(u, "prompt_tokens_details", None)
            if details:
                cached = getattr(details, "cached_tokens", 0) or 0
            usage = Usage(
                input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                cached_tokens=cached,
                output_tokens=getattr(u, "completion_tokens", 0) or 0,
            )
        return LLMResponse(content=final_blocks, usage=usage)

    # ------------------------------------------------------------------
    # generate_stream
    # ------------------------------------------------------------------

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        *,
        system_instructions: str = "",
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        """Stream a response via Chat Completions API."""

        chat_messages = self._serialize_messages_chat(messages)
        if system_instructions:
            chat_messages.insert(0, {"role": "system", "content": system_instructions})
        params: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        with open("/tmp/openai_prompt.json", "w") as f:
            json.dump(params, f, indent=2)
        if "temperature" in kwargs:
            params["temperature"] = kwargs.pop("temperature")
        else:
            params["temperature"] = self.temperature
        if self.max_tokens:
            params["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)

        chat_tools = self._serialize_tools_chat(tools)
        normalized_tool_choice = self._normalize_chat_tool_choice(tool_choice)
        if chat_tools:
            params["tools"] = chat_tools
            if normalized_tool_choice:
                params["tool_choice"] = normalized_tool_choice

        if response_format is not None and not chat_tools:
            params["response_format"] = {"type": "json_object"}

        collected_content = ""
        collected_tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"

        try:
            stream = await self.client.chat.completions.create(**params)
        except Exception as exc:
            detail = self._format_provider_error(exc)
            logger.exception("Stream chat completions request failed: %s", detail)
            raise RuntimeError(detail) from exc
        try:
            async for chunk in stream:
                # Usage-only chunk (arrives after all content when stream_options is set)
                if not chunk.choices and hasattr(chunk, "usage") and chunk.usage:
                    continue

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                chunk_finish = chunk.choices[0].finish_reason

                if delta.content:
                    collected_content += delta.content
                    yield TextDelta(text=delta.content)

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in collected_tool_calls:
                            collected_tool_calls[idx] = {
                                "id": tc_delta.id or "",
                                "name": "",
                                "arguments": "",
                            }
                        entry = collected_tool_calls[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                entry["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                entry["arguments"] += tc_delta.function.arguments

                if chunk_finish:
                    finish_reason = chunk_finish
        except Exception as exc:
            # Attempt to recover tool calls from a provider-side
            # ``tool_use_failed`` rejection (common with Groq when it
            # falls back to textual tool calls).
            recovered = self._try_recover_tool_use_failed(exc)
            if recovered is not None:
                collected_tool_calls, finish_reason = recovered
            else:
                detail = self._format_provider_error(exc)
                logger.exception("Stream chat completions iteration failed: %s", detail)
                raise RuntimeError(detail) from exc

        final_blocks: list[ContentBlock] = []
        if collected_content:
            final_blocks.append(TextBlock(text=collected_content))

        has_tool_calls = False
        if collected_tool_calls:
            has_tool_calls = True
            for _, tc_data in sorted(collected_tool_calls.items()):
                final_blocks.append(
                    ToolUseBlock(
                        call_id=tc_data["id"],
                        tool_name=tc_data["name"],
                        arguments=(
                            json.loads(tc_data["arguments"])
                            if tc_data["arguments"]
                            else {}
                        ),
                    )
                )

        if response_format is not None and collected_content and not has_tool_calls:
            try:
                parsed_obj = response_format.model_validate_json(collected_content)
                final_blocks.append(DataBlock(data=parsed_obj.model_dump(mode="json")))
            except Exception:
                logger.debug(
                    "Stream chat completions: failed to parse structured output: %s",
                    collected_content[:200],
                )

        yield CompletionEvent(content=final_blocks)
