"""OpenAI-compatible Chat Completions client — the canonical default LLMClient.

Implements the ``LLMClient`` kernel Protocol using the standard
``/v1/chat/completions`` endpoint.  Works with any provider that speaks
this API — Groq, OpenRouter, Ollama, vLLM, LM Studio, Together,
Fireworks, Mistral, DeepSeek, and vanilla OpenAI itself — including a
zero-API-key local Ollama/vLLM/LM Studio server, which is what makes this
the L1 default: one concrete implementation covers everything from "no
credentials, fully local" to "a real hosted provider," so ``agents/`` needs
no separate zero-infra LLM client. Additional vendor-native clients
(Anthropic, Gemini) and provider auto-detection live in
``integrations/llm/`` for the L2 case of wanting more than this default.

No inheritance from provider-specific clients.  Only imports:
  - ``openai`` SDK (AsyncOpenAI)
  - ``substrate.kernel.*`` (contracts and content types)
  - standard library
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, AsyncIterator, Optional

from openai import AsyncOpenAI

from substrate.agents.llm.modalities import fit_to_capabilities
from substrate.agents.llm.models import resolve_capabilities
from substrate.kernel import ChatMessage, ContentBlock
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.llm import (
    GenerationOptions,
    LLMResponse,
    ModelCapabilities,
    ReasoningEffort,
    Usage,
)
from substrate.kernel.core.content import (
    DataBlock,
    ErrorBlock,
    MediaBlock,
    ReasoningBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.messaging.stream import CompletionEvent, ReasoningDelta, TextDelta
from substrate.logger import setup_logging

logger = setup_logging()

_AUDIO_FORMATS = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3"}


def parse_tool_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Parse a model's tool-call arguments. Malformed JSON — common from
    smaller/local models — becomes an error to report back to the model,
    not an exception that kills the run."""
    if isinstance(raw, dict):
        return raw, None
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments were not valid JSON ({exc.msg}): {raw[:500]}"
    if not isinstance(parsed, dict):
        return {}, f"arguments must be a JSON object, got: {raw[:500]}"
    return parsed, None


def _data_uri(block: MediaBlock) -> str:
    return f"data:{block.media_type};base64,{base64.b64encode(block.data or b'').decode()}"


def _media_part(block: MediaBlock) -> dict[str, Any] | None:
    """A Chat Completions user-content part for *block*, or None when this
    API has no way to carry it (the caller substitutes a text note)."""
    if block.type == "image":
        if block.file_id:
            return None
        part: dict[str, Any] = {"url": block.url or _data_uri(block)}
        if block.detail != "auto":
            part["detail"] = block.detail
        return {"type": "image_url", "image_url": part}
    if block.type == "audio":
        fmt = _AUDIO_FORMATS.get(block.media_type)
        if block.data is None or fmt is None:
            return None
        return {
            "type": "input_audio",
            "input_audio": {"data": base64.b64encode(block.data).decode(), "format": fmt},
        }
    if block.type == "document":
        if block.file_id:
            return {"type": "file", "file": {"file_id": block.file_id}}
        if block.data is None:
            return None
        return {
            "type": "file",
            "file": {"file_data": _data_uri(block), "filename": block.filename or "document"},
        }
    return None


def _user_parts(blocks: Any) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append({"type": "text", "text": block.text})
        elif isinstance(block, DataBlock):
            parts.append({"type": "text", "text": json.dumps(block.data)})
        elif isinstance(block, MediaBlock):
            part = _media_part(block)
            if part is None:
                ref = block.filename or block.url or block.file_id or block.media_type
                part = {"type": "text", "text": f"[{block.type} not sent: {ref}]"}
            parts.append(part)
    return parts


def _tool_result_text(block: ToolResultBlock) -> str:
    texts: list[str] = []
    n_media = 0
    for b in block.content:
        if isinstance(b, TextBlock):
            texts.append(b.text)
        elif isinstance(b, DataBlock):
            texts.append(json.dumps(b.data))
        elif isinstance(b, ErrorBlock):
            texts.append(f"[{b.error_type}]: {b.message}")
        elif isinstance(b, MediaBlock):
            n_media += 1
    text = "\n".join(t for t in texts if t)
    if n_media:
        text += f"\n[{n_media} attachment(s) follow in the next message]"
    if block.is_error:
        text = f"Error: {text}"
    return text


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

    Use ``LLMFactory`` from ``substrate.integrations.llm`` for auto-wired
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
        http_client: Optional[Any] = None,
        capabilities: Optional[ModelCapabilities] = None,
    ) -> None:
        self.model = model
        # Unknown models (a local Ollama/vLLM model) resolve to text-only —
        # pass capabilities= to declare what such a model can really see.
        self.capabilities = capabilities or resolve_capabilities(model)
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
        if http_client is not None:
            # AsyncOpenAI's own supported seam for injecting a transport —
            # used by tests to point this client at a mocked server instead
            # of a real one, without touching any request-building code.
            client_kwargs["http_client"] = http_client

        self.client = AsyncOpenAI(**client_kwargs)

    # ── Message serialisation ─────────────────────────────────────────────────

    def _serialize_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Kernel messages → Chat Completions messages.

        Content the model can't see was already swapped for a text note by
        ``fit_to_capabilities``. Chat Completions tool messages are
        text-only, so media a tool returned is sent in one user message
        right after the contiguous run of tool messages (which must
        directly follow the assistant's tool calls).
        """
        messages = fit_to_capabilities(messages, self.capabilities)
        result: list[dict[str, Any]] = []
        pending_media: list[dict[str, Any]] = []

        def flush_tool_media() -> None:
            if pending_media:
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Attachments returned by the tool call(s) above:"},
                            *pending_media,
                        ],
                    }
                )
                pending_media.clear()

        for msg in messages:
            if msg.role != "tool":
                flush_tool_media()

            if msg.role == "system":
                text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
                result.append({"role": "system", "content": text})

            elif msg.role == "user":
                parts = _user_parts(msg.content)
                if len(parts) == 1 and parts[0]["type"] == "text":
                    result.append({"role": "user", "content": parts[0]["text"]})
                else:
                    result.append({"role": "user", "content": parts or ""})

            elif msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant"}
                text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                entry["content"] = "".join(text_parts) if text_parts else None
                tool_calls = [b for b in msg.content if isinstance(b, ToolUseBlock)]
                if not tool_calls and not text_parts:
                    # An empty reply. ``content: null`` with no tool calls is
                    # invalid, and would fail every later request in the session.
                    continue
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
                    if not isinstance(block, ToolResultBlock):
                        continue
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.call_id,
                            "content": _tool_result_text(block),
                        }
                    )
                    media = [b for b in block.content if isinstance(b, MediaBlock)]
                    if media:
                        label = block.name or block.call_id
                        pending_media.append({"type": "text", "text": f"From {label}:"})
                        pending_media.extend(_user_parts(media))
        flush_tool_media()
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

    # ── Request building ──────────────────────────────────────────────────────

    def _build_params(
        self, messages: list[ChatMessage], options: GenerationOptions, *, stream: bool
    ) -> dict[str, Any]:
        chat_messages = self._serialize_messages(messages)
        if options.system_instructions:
            chat_messages.insert(
                0, {"role": "system", "content": options.system_instructions}
            )
        params: dict[str, Any] = {"model": self.model, "messages": chat_messages}
        if stream:
            params["stream"] = True
            if self.provider in self._STREAM_USAGE_PROVIDERS:
                params["stream_options"] = {"include_usage": True}

        # Reasoning models reject any temperature but their default — only
        # send one there if the caller explicitly asked for it.
        if options.temperature is not None:
            params["temperature"] = options.temperature
        elif not self.capabilities.supports_reasoning:
            params["temperature"] = self.temperature

        # Only OpenAI itself documents ``reasoning_effort``; OpenAI-compatible
        # servers differ (or 400 on an unknown field), so they get nothing here
        # — pass a vendor-specific field through ``options.extra`` instead.
        if (
            options.reasoning is not None
            and self.provider == "openai"
            and self.capabilities.supports_reasoning
        ):
            params["reasoning_effort"] = (
                "minimal" if options.reasoning == ReasoningEffort.OFF else options.reasoning.value
            )

        max_tokens = options.max_tokens if options.max_tokens is not None else self.max_tokens
        if max_tokens:
            # OpenAI deprecated max_tokens (reasoning models reject it);
            # most OpenAI-compatible servers only understand max_tokens.
            key = "max_completion_tokens" if self.provider == "openai" else "max_tokens"
            params[key] = max_tokens

        serialized_tools = self._serialize_tools(_tools_to_dicts(options.tools))
        if serialized_tools:
            params["tools"] = serialized_tools
            normalized_choice = self._normalize_tool_choice(options.tool_choice)
            if normalized_choice:
                params["tool_choice"] = normalized_choice

        if options.response_format is not None and not serialized_tools:
            params["response_format"] = {"type": "json_object"}

        if options.extra:
            # The openai SDK validates top-level kwargs strictly — a
            # provider-specific field like llama-server's
            # `chat_template_kwargs` isn't a known param and raises
            # TypeError if merged directly into params. `extra_body` is
            # the SDK's own supported passthrough for exactly this case.
            params["extra_body"] = options.extra
        return params

    @staticmethod
    def _usage(u: Any) -> Usage:
        if not u:
            return Usage()
        prompt_details = getattr(u, "prompt_tokens_details", None)
        completion_details = getattr(u, "completion_tokens_details", None)
        return Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
            cached_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
            reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0) or 0,
        )

    @staticmethod
    def _tool_use_blocks(calls: list[tuple[str, str, Any]]) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for call_id, name, raw_args in calls:
            arguments, error = parse_tool_arguments(raw_args)
            blocks.append(
                ToolUseBlock(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    arguments_error=error,
                )
            )
        return blocks

    @staticmethod
    def _structured_block(
        response_format: Any, text: str, has_tool_calls: bool
    ) -> list[ContentBlock]:
        if response_format is None or not text or has_tool_calls:
            return []
        try:
            parsed = response_format.model_validate_json(text)
        except Exception:
            logger.debug("Failed to parse structured output: %s", text[:200])
            return []
        return [DataBlock(data=parsed.model_dump(mode="json"))]

    # ── LLMClient Protocol ────────────────────────────────────────────────────

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: RunMeta | None = None,
    ) -> LLMResponse:
        params = self._build_params(messages, options, stream=False)
        try:
            response = await self.client.chat.completions.create(**params)
        except Exception as exc:
            recovered = self._try_recover_tool_calls(exc)
            if recovered is not None:
                tc_dict, _ = recovered
                calls = [(tc["id"], tc["name"], tc["arguments"]) for _, tc in sorted(tc_dict.items())]
                return LLMResponse(content=self._tool_use_blocks(calls), usage=Usage())
            detail = self._format_error(exc)
            logger.exception("Chat completions request failed: %s", detail)
            raise RuntimeError(detail) from exc

        msg = response.choices[0].message
        blocks: list[ContentBlock] = []
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        if reasoning:
            blocks.append(ReasoningBlock(text=reasoning))
        if msg.content:
            blocks.append(TextBlock(text=msg.content))
        calls = [
            (tc.id or "", tc.function.name, tc.function.arguments)
            for tc in (msg.tool_calls or [])
        ]
        blocks.extend(self._tool_use_blocks(calls))
        blocks.extend(
            self._structured_block(options.response_format, msg.content or "", bool(calls))
        )
        return LLMResponse(content=blocks, usage=self._usage(getattr(response, "usage", None)))

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: RunMeta | None = None,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        params = self._build_params(messages, options, stream=True)
        collected_content = ""
        collected_reasoning = ""
        collected_tool_calls: dict[int, dict[str, Any]] = {}
        usage = Usage()

        try:
            stream = await self.client.chat.completions.create(**params)
        except Exception as exc:
            detail = self._format_error(exc)
            logger.exception("Stream chat completions request failed: %s", detail)
            raise RuntimeError(detail) from exc

        try:
            async for chunk in stream:
                # With include_usage the final chunk has no choices, only usage.
                if getattr(chunk, "usage", None):
                    usage = self._usage(chunk.usage)
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                # DeepSeek/Qwen-style servers (vLLM, Ollama) stream reasoning here.
                reasoning = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if reasoning:
                    collected_reasoning += reasoning
                    yield ReasoningDelta(text=reasoning)
                if delta.content:
                    collected_content += delta.content
                    yield TextDelta(text=delta.content)

                for tc_delta in delta.tool_calls or []:
                    entry = collected_tool_calls.setdefault(
                        tc_delta.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments
        except Exception as exc:
            recovered = self._try_recover_tool_calls(exc)
            if recovered is None:
                detail = self._format_error(exc)
                logger.exception("Stream iteration failed: %s", detail)
                raise RuntimeError(detail) from exc
            collected_tool_calls, _ = recovered

        blocks: list[ContentBlock] = []
        if collected_reasoning:
            blocks.append(ReasoningBlock(text=collected_reasoning))
        if collected_content:
            blocks.append(TextBlock(text=collected_content))
        calls = [
            (tc["id"], tc["name"], tc["arguments"])
            for _, tc in sorted(collected_tool_calls.items())
        ]
        blocks.extend(self._tool_use_blocks(calls))
        blocks.extend(
            self._structured_block(options.response_format, collected_content, bool(calls))
        )
        yield CompletionEvent(content=blocks, usage=usage)

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
