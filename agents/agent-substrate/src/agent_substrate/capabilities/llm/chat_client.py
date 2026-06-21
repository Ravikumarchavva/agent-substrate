"""OpenAI-compatible Chat Completions client.

Implements the ``LLMClient`` kernel Protocol using the standard
``/v1/chat/completions`` endpoint.  Works with any provider that speaks
this API — Groq, OpenRouter, Ollama, vLLM, LM Studio, Together,
Fireworks, Mistral, DeepSeek, and vanilla OpenAI itself.

No inheritance from provider-specific clients.  Only imports:
  - ``openai`` SDK (AsyncOpenAI)
  - ``agent_substrate.kernel.*`` (contracts and content types)
  - standard library
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from openai import AsyncOpenAI

from agent_substrate.kernel import ChatMessage, ContentBlock
from agent_substrate.kernel.llm import GenerationOptions, LLMResponse, Usage
from agent_substrate.kernel.core.content import (
    DataBlock,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agent_substrate.kernel.messaging.stream import CompletionEvent, TextDelta
from agent_substrate.logger import setup_logging

if TYPE_CHECKING:
    pass

logger = setup_logging()


def _tools_to_dicts(tools: Any) -> Optional[list[dict[str, Any]]]:
    if not tools:
        return None
    return [
        {"name": t.name, "description": t.description, "parameters": t.input_schema}
        for t in tools
    ]


# ── Strict-schema helpers (inlined — no integrations dependency) ──────────────


def _make_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert an optional property to a required-but-nullable schema."""
    out = dict(schema)
    t = out.get("type")
    if isinstance(t, str) and t != "null":
        out["type"] = [t, "null"]
        return out
    if isinstance(t, list) and "null" not in t:
        out["type"] = [*t, "null"]
        return out
    for key in ("anyOf", "oneOf"):
        opts = out.get(key)
        if isinstance(opts, list) and not any(
            isinstance(o, dict) and o.get("type") == "null" for o in opts
        ):
            out[key] = [*opts, {"type": "null"}]
            return out
    return out


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively normalize a JSON schema for OpenAI strict function calling."""
    out = dict(schema)

    if out.get("type") == "object":
        out["additionalProperties"] = False
        props = out.get("properties")
        if isinstance(props, dict):
            required = set(out.get("required", []))
            new_props: dict[str, Any] = {}
            for k, v in props.items():
                prop = _strict_schema(v) if isinstance(v, dict) else v
                if k not in required and isinstance(prop, dict):
                    prop = _make_nullable(prop)
                new_props[k] = prop
            out["properties"] = new_props
            out["required"] = list(props.keys())

    for key in ("items", "additionalProperties", "not"):
        v = out.get(key)
        if isinstance(v, dict):
            out[key] = _strict_schema(v)

    for key in ("properties", "$defs", "definitions"):
        v = out.get(key)
        if isinstance(v, dict):
            out[key] = {
                ik: _strict_schema(iv) if isinstance(iv, dict) else iv
                for ik, iv in v.items()
            }

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        v = out.get(key)
        if isinstance(v, list):
            out[key] = [_strict_schema(i) if isinstance(i, dict) else i for i in v]

    return out


# ── Client ────────────────────────────────────────────────────────────────────


class OpenAIChatCompletionClient:
    """Universal LLM client for any OpenAI Chat Completions-compatible provider.

    Point it at any server that speaks ``POST /v1/chat/completions``::

        # Ollama (local)
        client = OpenAIChatCompletionClient(
            model="llama3.2", api_key="ollama",
            base_url="http://localhost:11434/v1",
        )

        # Groq (cloud)
        client = OpenAIChatCompletionClient(
            model="llama-3.3-70b-versatile", api_key=groq_key,
            base_url="https://api.groq.com/openai/v1",
        )

        # DeepSeek
        client = OpenAIChatCompletionClient(
            model="deepseek-chat", api_key=deepseek_key,
            base_url="https://api.deepseek.com/v1",
        )

    Use ``LLMFactory`` from ``agent_substrate.integrations.llm`` for auto-wired
    provider detection by model-name prefix.
    """

    # Providers that support OpenAI's ``strict: true`` tool-call mode.
    _STRICT_PROVIDERS: frozenset[str] = frozenset({"openai"})
    # Providers that support ``stream_options: {include_usage: true}``.
    _STREAM_USAGE_PROVIDERS: frozenset[str] = frozenset(
        {"openai", "groq", "openrouter", "together", "fireworks"}
    )

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        *,
        base_url: Optional[str] = None,
        organization: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.api_key = api_key
        # Provider tag set by LLMFactory so strict-mode logic can branch.
        self.provider: str = "openai" if not base_url else "compatible"

        client_kwargs: dict[str, Any] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        if organization:
            client_kwargs["organization"] = organization
        if extra_headers:
            client_kwargs["default_headers"] = extra_headers
        if timeout is not None:
            client_kwargs["timeout"] = timeout

        self.client = AsyncOpenAI(**client_kwargs)

    # ── Message serialisation ─────────────────────────────────────────────────

    @staticmethod
    def _serialize_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
                result.append({"role": "system", "content": text})

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
                    result.append({"role": "user", "content": parts or ""})

            elif msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant"}
                text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                entry["content"] = "".join(text_parts) if text_parts else None
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
                        if isinstance(block.content, list):
                            parts_list = []
                            for b in block.content:
                                if isinstance(b, dict) and b.get("type") == "text":
                                    parts_list.append(b["text"])
                                elif hasattr(b, "text"):
                                    parts_list.append(getattr(b, "text"))
                            content_str = "\n".join(parts_list)
                        else:
                            content_str = block.content or ""
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.call_id,
                                "content": content_str,
                            }
                        )
        return result

    # ── Tool schema serialisation ─────────────────────────────────────────────

    def _serialize_tools(
        self, tools: Optional[list[dict[str, Any]]]
    ) -> Optional[list[dict[str, Any]]]:
        if not tools:
            return None
        use_strict = self.provider in self._STRICT_PROVIDERS
        result: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                fn = dict(tool["function"])
                if "parameters" in fn:
                    fn["parameters"] = (
                        _strict_schema(fn["parameters"])
                        if use_strict
                        else fn["parameters"]
                    )
                    fn.pop("strict", None)
                if use_strict:
                    fn["strict"] = True
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
                    "parameters": _strict_schema(params) if use_strict else params,
                }
                if use_strict:
                    fn_dict["strict"] = True
                result.append({"type": "function", "function": fn_dict})
            else:
                result.append(tool)
        return result

    @staticmethod
    def _normalize_tool_choice(
        tool_choice: Optional[str | dict[str, Any]],
    ) -> Optional[str | dict[str, Any]]:
        if not tool_choice:
            return None
        if isinstance(tool_choice, str):
            if tool_choice in {"auto", "required", "none"}:
                return tool_choice
            return {"type": "function", "function": {"name": tool_choice}}
        return tool_choice

    # ── Error helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _format_error(exc: Exception) -> str:
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
            status = getattr(response, "status_code", None)
            if status:
                parts.append(f"status={status}")
            text = getattr(response, "text", None)
            if text:
                parts.append(f"response={text}")
        return " | ".join(p for p in parts if p)

    @staticmethod
    def _try_recover_tool_calls(
        exc: Exception,
    ) -> Optional[tuple[dict[int, dict[str, Any]], str]]:
        """Recover tool calls from Groq's ``tool_use_failed`` textual fallback."""
        body = getattr(exc, "body", None)
        if not isinstance(body, dict) or body.get("code") != "tool_use_failed":
            return None
        raw: str = body.get("failed_generation", "")
        if not raw:
            return None
        pattern = re.compile(
            r"<function=(\w+)\s*(\{.*?\})\s*(?:</function>|/>)", re.DOTALL
        )
        calls: dict[int, dict[str, Any]] = {}
        for idx, m in enumerate(pattern.finditer(raw)):
            try:
                arguments = json.loads(m.group(2))
            except json.JSONDecodeError:
                return None
            calls[idx] = {
                "id": f"recovered_{idx}",
                "name": m.group(1),
                "arguments": json.dumps(arguments)
                if isinstance(arguments, dict)
                else str(arguments),
            }
        if not calls:
            return None
        logger.info("Recovered %d tool call(s) from tool_use_failed", len(calls))
        return calls, "tool_calls"

    # ── LLMClient Protocol ────────────────────────────────────────────────────

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        tool_dicts = _tools_to_dicts(options.tools)
        chat_messages = self._serialize_messages(messages)
        if options.system_instructions:
            chat_messages.insert(
                0, {"role": "system", "content": options.system_instructions}
            )

        params: dict[str, Any] = {"model": self.model, "messages": chat_messages}
        params["temperature"] = (
            options.temperature if options.temperature is not None else self.temperature
        )
        if options.max_tokens is not None:
            params["max_tokens"] = options.max_tokens
        elif self.max_tokens:
            params["max_tokens"] = self.max_tokens

        serialized_tools = self._serialize_tools(tool_dicts)
        normalized_choice = self._normalize_tool_choice(options.tool_choice)
        if serialized_tools:
            params["tools"] = serialized_tools
            if normalized_choice:
                params["tool_choice"] = normalized_choice

        response_format = options.response_format
        if response_format is not None and not serialized_tools:
            params["response_format"] = {"type": "json_object"}

        try:
            response = await self.client.chat.completions.create(**params)
        except Exception as exc:
            recovered = self._try_recover_tool_calls(exc)
            if recovered is not None:
                tc_dict, _ = recovered
                blocks: list[ContentBlock] = [
                    ToolUseBlock(
                        call_id=tc["id"],
                        tool_name=tc["name"],
                        arguments=json.loads(tc["arguments"])
                        if tc["arguments"]
                        else {},
                    )
                    for _, tc in sorted(tc_dict.items())
                ]
                return LLMResponse(content=blocks, usage=Usage())
            detail = self._format_error(exc)
            logger.exception("Chat completions request failed: %s", detail)
            raise RuntimeError(detail) from exc

        choice = response.choices[0]
        msg = choice.message
        final_blocks: list[ContentBlock] = []

        if msg.content:
            final_blocks.append(TextBlock(text=msg.content))

        has_tool_calls = False
        if msg.tool_calls:
            has_tool_calls = True
            for tc in msg.tool_calls:
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
                parsed = response_format.model_validate_json(msg.content)
                final_blocks.append(DataBlock(data=parsed.model_dump(mode="json")))
            except Exception:
                logger.debug("Failed to parse structured output: %s", msg.content[:200])

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

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        tool_dicts = _tools_to_dicts(options.tools)
        chat_messages = self._serialize_messages(messages)
        if options.system_instructions:
            chat_messages.insert(
                0, {"role": "system", "content": options.system_instructions}
            )

        params: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "stream": True,
        }
        if self.provider in self._STREAM_USAGE_PROVIDERS:
            params["stream_options"] = {"include_usage": True}
        params["temperature"] = (
            options.temperature if options.temperature is not None else self.temperature
        )
        if options.max_tokens is not None:
            params["max_tokens"] = options.max_tokens
        elif self.max_tokens:
            params["max_tokens"] = self.max_tokens

        serialized_tools = self._serialize_tools(tool_dicts)
        normalized_choice = self._normalize_tool_choice(options.tool_choice)
        if serialized_tools:
            params["tools"] = serialized_tools
            if normalized_choice:
                params["tool_choice"] = normalized_choice

        response_format = options.response_format
        if response_format is not None and not serialized_tools:
            params["response_format"] = {"type": "json_object"}

        collected_content = ""
        collected_tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"

        try:
            stream = await self.client.chat.completions.create(**params)
        except Exception as exc:
            detail = self._format_error(exc)
            logger.exception("Stream chat completions request failed: %s", detail)
            raise RuntimeError(detail) from exc

        try:
            async for chunk in stream:
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
                                "id": "",
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
            recovered = self._try_recover_tool_calls(exc)
            if recovered is not None:
                collected_tool_calls, finish_reason = recovered
            else:
                detail = self._format_error(exc)
                logger.exception("Stream iteration failed: %s", detail)
                raise RuntimeError(detail) from exc

        final_blocks: list[ContentBlock] = []
        if collected_content:
            final_blocks.append(TextBlock(text=collected_content))

        has_tool_calls = False
        if collected_tool_calls:
            has_tool_calls = True
            for _, tc in sorted(collected_tool_calls.items()):
                final_blocks.append(
                    ToolUseBlock(
                        call_id=tc["id"],
                        tool_name=tc["name"],
                        arguments=json.loads(tc["arguments"])
                        if tc["arguments"]
                        else {},
                    )
                )

        if response_format is not None and collected_content and not has_tool_calls:
            try:
                parsed = response_format.model_validate_json(collected_content)
                final_blocks.append(DataBlock(data=parsed.model_dump(mode="json")))
            except Exception:
                logger.debug(
                    "Stream: failed to parse structured output: %s",
                    collected_content[:200],
                )

        yield CompletionEvent(content=final_blocks)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        """Estimate token count using tiktoken (cl100k_base for unknown models)."""
        try:
            import tiktoken

            try:
                enc = tiktoken.encoding_for_model(self.model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")
            total = 0
            for msg in messages:
                total += 4  # per-message overhead
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        total += len(enc.encode(block.text))
            return total
        except ImportError:
            # tiktoken not available — rough word-based estimate
            total_chars = sum(
                len(b.text)
                for msg in messages
                for b in msg.content
                if isinstance(b, TextBlock)
            )
            return total_chars // 4
