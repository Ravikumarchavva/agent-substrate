"""OpenAI model client implementation."""

from __future__ import annotations
from substrate.logger import setup_logging

import io
import json
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator, Optional, cast

import tiktoken
from openai import AsyncOpenAI
from openai.types.responses.response_completed_event import ResponseCompletedEvent
from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
from openai.types.responses.response_reasoning_summary_text_delta_event import (
    ResponseReasoningSummaryTextDeltaEvent,
)

from substrate.agents.llm.chat_client import parse_tool_arguments
from substrate.agents.llm.modalities import fit_to_capabilities
from substrate.agents.llm.models import resolve_capabilities
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.llm import (
    GenerationOptions,
    LLMClient,
    LLMResponse,
    ModelCapabilities,
    ReasoningEffort,
    Usage,
)
from substrate.kernel import ChatMessage, ContentBlock
from substrate.kernel.tools.tools import Tool, is_hosted_tool, is_provider_defined_tool
from substrate.kernel.core.content import (
    TextBlock,
    ToolUseBlock,
    DataBlock,
    ReasoningBlock,
)
from substrate.kernel.messaging.stream import TextDelta, ReasoningDelta, CompletionEvent
from substrate.integrations.llm.encoders.openai import (
    encode_messages as _encode_messages,
    encode_tools as _encode_tools,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = setup_logging()

# ── MIME helper ───────────────────────────────────────────────────────────────


def _mime_for(filename: str) -> str:
    """Return a plausible MIME type based on the file extension."""
    ext = filename.rsplit(".", 1)[-1].lower()
    return {
        "mp3": "audio/mpeg",
        "mp4": "audio/mp4",
        "m4a": "audio/mp4",
        "mpeg": "audio/mpeg",
        "mpga": "audio/mpeg",
        "wav": "audio/wav",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
    }.get(ext, "application/octet-stream")


def _normalize_strict_json_schema(schema: Any) -> Any:
    """Recursively normalize a JSON schema for OpenAI strict mode.

    OpenAI strict structured output mode requires:
    - ``additionalProperties: false`` on every object schema.
    - All keys in ``properties`` to be explicitly present in ``required``
      (true optionality is expressed via nullable unions with null).
    """
    if isinstance(schema, dict):
        normalized = {
            key: _normalize_strict_json_schema(value) for key, value in schema.items()
        }

        if normalized.get("type") == "object":
            normalized.setdefault("additionalProperties", False)
            if "properties" in normalized and isinstance(normalized["properties"], dict):
                normalized["required"] = list(normalized["properties"].keys())

        for key in ("properties", "$defs", "definitions"):
            if key in normalized and isinstance(normalized[key], dict):
                normalized[key] = {
                    item_key: _normalize_strict_json_schema(item_value)
                    for item_key, item_value in normalized[key].items()
                }

        for key in ("items", "additionalProperties", "not"):
            if key in normalized:
                normalized[key] = _normalize_strict_json_schema(normalized[key])

        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            if key in normalized and isinstance(normalized[key], list):
                normalized[key] = [
                    _normalize_strict_json_schema(item) for item in normalized[key]
                ]

        return normalized

    if isinstance(schema, list):
        return [_normalize_strict_json_schema(item) for item in schema]

    return schema


def _build_openai_text_format(response_format: type["BaseModel"]) -> dict[str, Any]:
    """Convert a Pydantic model to OpenAI Responses API text.format config."""
    schema_dict = _normalize_strict_json_schema(response_format.model_json_schema())
    return {
        "format": {
            "type": "json_schema",
            "name": response_format.__name__,
            "strict": True,
            "schema": schema_dict,
        }
    }


def _tools_from_options(options: "GenerationOptions") -> Optional[list[dict[str, Any]]]:
    if not options.tools:
        return None
    # HostedTool / ProviderDefinedTool have no input_schema — this client only
    # advertises local Tool schemas here; hosted/provider-defined tools aren't
    # wired into this code path yet.
    local_tools = [
        cast(Tool, t)
        for t in options.tools
        if not is_hosted_tool(t) and not is_provider_defined_tool(t)
    ]
    if not local_tools:
        return None
    return [
        {"name": t.name, "description": t.description, "parameters": t.input_schema}
        for t in local_tools
    ]


class OpenAIClient(LLMClient):
    """OpenAI API client — text, vision, and audio in one place.

    A single ``AsyncOpenAI`` instance is used for all operations:
      • ``generate`` / ``generate_stream`` → Responses API (text + vision)
      • ``transcribe``                      → ``client.audio.transcriptions``
      • ``stream_tts``                      → ``client.audio.speech``
      • ``create_s2s_session`` / ``s2s_ws_url`` → OpenAI Realtime API
    """

    _REALTIME_UPSTREAM = "wss://api.openai.com/v1/realtime"

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        *,
        base_url: Optional[str] = None,
        organization: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        default_stt_model: str = "whisper-1",
        default_tts_model: str = "gpt-4o-mini-tts",
        default_voice: str = "coral",
        default_tts_format: str = "mp3",
        realtime_model: str = "gpt-4o-realtime-preview-2024-12-17",
        capabilities: Optional[ModelCapabilities] = None,
        **kwargs,
    ):
        self.model = model
        self.capabilities = capabilities or resolve_capabilities(model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key  # stored so WorkflowRunner can build sibling clients
        self.base_url = base_url

        # Build AsyncOpenAI with optional overrides (base_url enables
        # vLLM, Ollama, Together, Perplexity, etc.)
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
        self._default_stt_model = default_stt_model
        self._default_tts_model = default_tts_model
        self._default_voice = default_voice
        self._default_tts_format = default_tts_format
        self._realtime_model = realtime_model
        self._encoding = None

    # ── Audio capability flags ────────────────────────────────────────────────

    @property
    def supports_audio(self) -> bool:
        return True

    @property
    def supports_s2s(self) -> bool:
        return True

    def _get_encoding(self):
        """Lazy load tiktoken encoding."""
        if self._encoding is None:
            try:
                self._encoding = tiktoken.encoding_for_model(self.model)
            except KeyError:
                self._encoding = tiktoken.get_encoding("cl100k_base")
        return self._encoding

    # ------------------------------------------------------------------
    # Private helpers — delegates to ``core.messages.encoders.openai``
    # ------------------------------------------------------------------

    def _serialize_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Serialise framework messages into (instructions, conversation_input).

        Pure: images (including tool-returned ones) are sent inline as data
        URLs — nothing is uploaded to the provider as a side effect.
        """
        return _encode_messages(fit_to_capabilities(messages, self.capabilities))

    def _serialize_tools(
        self, tools: Optional[list[dict[str, Any]]]
    ) -> Optional[list[dict[str, Any]]]:
        """Normalise tool dicts to Responses API flattened format.

        Delegates to the centralised OpenAI encoder.
        """
        return _encode_tools(tools)

    @staticmethod
    def _normalize_tool_choice(
        tool_choice: Optional[str | dict[str, Any]],
    ) -> Optional[str | dict[str, Any]]:
        """Translate named-tool forcing into the Responses API shape."""
        if not tool_choice:
            return None
        if isinstance(tool_choice, str):
            if tool_choice in {"auto", "required", "none"}:
                return tool_choice
            return {"type": "function", "name": tool_choice}
        return tool_choice

    @staticmethod
    def _extract_usage(response: Any) -> Usage:
        u = getattr(response, "usage", None)
        if u is None:
            return Usage()
        input_details = getattr(u, "input_tokens_details", None)
        output_details = getattr(u, "output_tokens_details", None)
        return Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            cached_tokens=getattr(input_details, "cached_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            reasoning_tokens=getattr(output_details, "reasoning_tokens", 0) or 0,
        )

    @staticmethod
    def _tool_use_block(item: Any) -> ToolUseBlock:
        arguments, error = parse_tool_arguments(item.arguments)
        return ToolUseBlock(
            call_id=getattr(item, "call_id", "") or getattr(item, "id", ""),
            tool_name=item.name,
            arguments=arguments,
            arguments_error=error,
        )

    def _reasoning_param(self, effort: ReasoningEffort | None) -> dict[str, Any] | None:
        """``reasoning`` request field, or None. Only sent when the caller set
        ``options.reasoning`` and the model reasons: it also turns on reasoning
        *summaries*, which some organizations must be verified to receive, so
        it stays opt-in."""
        if effort is None or not self.capabilities.supports_reasoning:
            return None
        if effort == ReasoningEffort.OFF:
            return {"effort": "minimal" if self.model.startswith("gpt-5") else "low"}
        return {"effort": effort.value, "summary": "auto"}

    def _build_params(
        self, messages: list[ChatMessage], options: GenerationOptions, *, stream: bool
    ) -> dict[str, Any]:
        _, conversation_input = self._serialize_messages(messages)
        params: dict[str, Any] = {"model": self.model, "input": conversation_input}
        if stream:
            params["stream"] = True

        # Reasoning models reject a custom temperature
        if options.temperature is not None:
            params["temperature"] = options.temperature
        elif not self.capabilities.supports_reasoning:
            params["temperature"] = self.temperature

        if options.system_instructions:
            params["instructions"] = options.system_instructions
        max_tokens = options.max_tokens or self.max_tokens
        if max_tokens:
            params["max_output_tokens"] = max_tokens

        tools = self._serialize_tools(_tools_from_options(options))
        if tools:
            params["tools"] = tools
            choice = self._normalize_tool_choice(options.tool_choice)
            if choice:
                params["tool_choice"] = choice
        # Tools and a response schema can be combined: the model calls tools
        # as needed and its final text answer is schema-conformant.
        if options.response_format is not None:
            params["text"] = _build_openai_text_format(options.response_format)

        reasoning = self._reasoning_param(options.reasoning)
        if reasoning:
            params["reasoning"] = reasoning
        return params

    def _parse_output(
        self, response: Any, response_format: type[BaseModel] | None
    ) -> list[ContentBlock]:
        """Responses API output → content blocks (reasoning summary, text, tool
        calls, structured data), in that order."""
        blocks: list[ContentBlock] = []
        tool_calls: list[ContentBlock] = []
        summaries: list[str] = []
        for item in response.output or []:
            if item.type == "function_call":
                tool_calls.append(self._tool_use_block(item))
            elif item.type == "reasoning":
                summaries.extend(
                    part.text for part in (getattr(item, "summary", None) or []) if part.text
                )
            # tool_search_call / tool_search_output are metadata items produced
            # by OpenAI hosted tool search (gpt-5.4+) — skipped; the actual
            # function_call follows in the same output list.
        if summaries:
            blocks.append(ReasoningBlock(text="\n\n".join(summaries)))
        text = getattr(response, "output_text", "") or ""
        if text:
            blocks.append(TextBlock(text=text))
        blocks.extend(tool_calls)
        if response_format is not None and text and not tool_calls:
            try:
                parsed = response_format.model_validate_json(text)
                blocks.append(DataBlock(data=parsed.model_dump(mode="json")))
            except Exception:
                logger.debug("Failed to parse structured output: %s", text[:200])
        return blocks

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: RunMeta | None = None,
    ) -> LLMResponse:
        """Generate a single response from OpenAI using Responses API."""
        params = self._build_params(messages, options, stream=False)
        response = await self.client.responses.create(**params)
        return LLMResponse(
            content=self._parse_output(response, options.response_format),
            usage=self._extract_usage(response),
        )

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
        final_response = None

        stream = await self.client.responses.create(**params)
        async for event in stream:
            if isinstance(event, ResponseTextDeltaEvent):
                if event.delta:
                    yield TextDelta(text=event.delta)
            elif isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
                if event.delta:
                    yield ReasoningDelta(text=event.delta)
            elif isinstance(event, ResponseCompletedEvent):
                final_response = event.response

        if final_response is None:
            logger.error(
                "No ResponseCompletedEvent received from model %s — "
                "the provider may not support the Responses API.",
                self.model,
            )
            raise RuntimeError(
                f"LLM stream for model '{self.model}' ended without producing "
                f"a completion. The provider may not support the OpenAI "
                f"Responses API. Check server logs for details."
            )
        yield CompletionEvent(
            content=self._parse_output(final_response, options.response_format),
            usage=self._extract_usage(final_response),
        )

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        """Count tokens using tiktoken."""
        encoding = self._get_encoding()
        num_tokens = 0

        for message in messages:
            # Every message follows <im_start>{role/name}\n{content}<im_end>\n
            num_tokens += 4
            msg_dict = message.model_dump(mode="json")

            for key, value in msg_dict.items():
                if isinstance(value, str):
                    num_tokens += len(encoding.encode(value))
                elif key == "content" and isinstance(value, list):
                    # We need to serialize blocks to string, or just use json dump
                    num_tokens += len(encoding.encode(json.dumps(value)))

        num_tokens += 2  # Every reply is primed with <im_start>assistant
        return num_tokens

    # ── Audio: Transcription (STT) ────────────────────────────────────────────

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        model: str = "",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """Transcribe audio via Whisper / GPT-4o-transcribe."""
        effective_model = model or self._default_stt_model
        logger.info(
            "Transcribing audio: file=%s model=%s bytes=%d",
            filename,
            effective_model,
            len(audio_bytes),
        )
        kwargs: dict = {}
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        file_tuple = (filename, io.BytesIO(audio_bytes), _mime_for(filename))
        result = await self.client.audio.transcriptions.create(
            model=effective_model,
            file=file_tuple,
            response_format="text",
            **kwargs,
        )
        text: str = (
            result if isinstance(result, str) else getattr(result, "text", str(result))
        )
        logger.info("Transcription complete: %d chars", len(text))
        return text.strip()

    # ── Audio: Text-to-Speech (TTS) ───────────────────────────────────────────

    async def stream_tts(
        self,
        *,
        text: str,
        voice: str = "",
        model: str = "",
        response_format: str = "",
        instructions: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """Stream TTS audio chunks via OpenAI's speech synthesis API."""
        effective_model = model or self._default_tts_model
        effective_voice = voice or self._default_voice
        effective_fmt = response_format or self._default_tts_format
        logger.info(
            "TTS request: model=%s voice=%s format=%s chars=%d",
            effective_model,
            effective_voice,
            effective_fmt,
            len(text),
        )
        kwargs: dict = {}
        if instructions and effective_model == "gpt-4o-mini-tts":
            kwargs["instructions"] = instructions
        async with self.client.audio.speech.with_streaming_response.create(
            model=effective_model,
            voice=effective_voice,
            input=text,
            response_format=effective_fmt,  # type: ignore[arg-type]
            **kwargs,
        ) as resp:
            async for chunk in resp.iter_bytes(chunk_size=4096):
                yield chunk

    # ── Audio: Speech-to-Speech (S2S / Realtime) ─────────────────────────────

    async def create_s2s_session(
        self,
        *,
        model: str = "",
        voice: str = "",
        instructions: Optional[str] = None,
    ) -> dict:
        """Mint a short-lived ephemeral token for an OpenAI Realtime S2S session."""
        import httpx

        effective_model = model or self._realtime_model
        effective_voice = voice or self._default_voice
        logger.info(
            "Creating S2S session: model=%s voice=%s", effective_model, effective_voice
        )
        body: dict = {
            "model": effective_model,
            "voice": effective_voice,
            "modalities": ["audio", "text"],
            "turn_detection": {"type": "server_vad"},
        }
        if instructions:
            body["instructions"] = instructions
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                "https://api.openai.com/v1/realtime/sessions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self.client.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
        logger.info("S2S session created, expires_at=%s", data.get("expires_at"))
        return data

    def s2s_ws_url(self, model: str) -> str:
        """Return the OpenAI Realtime WebSocket URL for S2S sessions."""
        return f"{self._REALTIME_UPSTREAM}?model={model}"

    # ── Vision: Image generation ──────────────────────────────────────────────

    @property
    def supports_image_generation(self) -> bool:
        return True

    async def generate_image(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: str = "1024x1024",
        quality: str = "standard",
        style: Optional[str] = None,
        model: str = "dall-e-3",
        **kwargs,
    ) -> list[str]:
        """Generate images from a text prompt via the OpenAI Images API.

        Returns a list of URLs (for DALL-E 3, always length 1 per request) or
        base-64 data URL strings when ``response_format="b64_json"`` is passed
        in ``kwargs``.

        Examples::

            urls = await client.generate_image("a cat wearing a space helmet")
            urls = await client.generate_image(
                "product shot on white background",
                model="gpt-image-1",
                size="1024x1024",
                quality="high",
            )
        """
        effective_model = model or "dall-e-3"
        logger.info(
            "Generating image: model=%s n=%d size=%s quality=%s",
            effective_model,
            n,
            size,
            quality,
        )

        params: dict = {
            "model": effective_model,
            "prompt": prompt,
            "n": n,
            "size": size,
        }

        # ``gpt-image-1`` uses ``quality`` with values "low"/"medium"/"high"/"auto".
        # DALL-E 3 uses "standard" / "hd"; DALL-E 2 ignores the param.
        if quality:
            params["quality"] = quality

        # ``style`` only supported by DALL-E 3 ("vivid" / "natural").
        if style and effective_model == "dall-e-3":
            params["style"] = style

        # Allow callers to override response_format, etc.
        params.update(kwargs)

        response = await self.client.images.generate(**params)

        results: list[str] = []
        for item in response.data:
            if getattr(item, "url", None):
                results.append(item.url)  # type: ignore[arg-type]
            elif getattr(item, "b64_json", None):
                results.append(f"data:image/png;base64,{item.b64_json}")
        logger.info("Image generation complete: %d result(s)", len(results))
        return results
