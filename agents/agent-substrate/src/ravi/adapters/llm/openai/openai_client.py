"""OpenAI model client implementation."""

from __future__ import annotations
from ravi.logger import setup_logging

import hashlib
import io
import json
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator, Optional

import tiktoken
from openai import AsyncOpenAI
from openai.types.responses.response_completed_event import ResponseCompletedEvent
from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
from openai.types.responses.response_reasoning_summary_text_delta_event import (
    ResponseReasoningSummaryTextDeltaEvent,
)

from ravi.fabric.llm.client import LLMClient
from ravi.kernel import ChatMessage, ContentBlock
from ravi.kernel.content import (
    TextBlock,
    ImageBlock,
    ToolUseBlock,
    DataBlock,
    ToolResultBlock,
)
from ravi.kernel.stream import TextDelta, ReasoningDelta, CompletionEvent
from ravi.adapters.llm.encoders.openai import (
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

    OpenAI's strict structured-output mode requires every object schema to
    declare ``additionalProperties: false``. Pydantic's ``model_json_schema()``
    does not guarantee that on all nested object nodes, so we add it here.
    """
    if isinstance(schema, dict):
        normalized = {
            key: _normalize_strict_json_schema(value) for key, value in schema.items()
        }

        if normalized.get("type") == "object":
            normalized.setdefault("additionalProperties", False)

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
        **kwargs,
    ):
        self.model = model
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
        self._uploaded_image_file_ids: dict[str, str] = {}

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

    def _messages_to_openai_format(
        self, messages: list[ChatMessage]
    ) -> list[dict]:
        """Convert framework messages to OpenAI API format.

        .. deprecated:: Use ``_serialize_messages`` instead.
        """
        return [msg.model_dump(mode="json") for msg in messages]

    def _tools_to_openai_format(
        self, tools: Optional[list[dict[str, Any]]]
    ) -> Optional[list[dict[str, Any]]]:
        """Convert tools to OpenAI function calling format.

        .. deprecated:: Use ``_serialize_tools`` instead.
        """
        if not tools:
            return None
        return tools

    # ------------------------------------------------------------------
    # Private helpers — delegates to ``core.messages.encoders.openai``
    # ------------------------------------------------------------------

    @staticmethod
    def _image_extension(media_type: str) -> str:
        return {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
            "image/gif": "gif",
        }.get(media_type, "bin")

    async def _upload_image_input(
        self,
        *,
        image_bytes: bytes,
        media_type: str,
        filename: str,
    ) -> str:
        cache_key = hashlib.sha256(
            media_type.encode("utf-8") + b"\0" + image_bytes
        ).hexdigest()
        cached = self._uploaded_image_file_ids.get(cache_key)
        if cached:
            return cached

        uploaded = await self.client.files.create(
            file=(filename, image_bytes, media_type),
            purpose="user_data",
        )
        self._uploaded_image_file_ids[cache_key] = uploaded.id
        return uploaded.id

    async def _materialize_tool_result_media(
        self,
        messages: list[ChatMessage],
    ) -> list[ChatMessage]:
        prepared: list[ChatMessage] = []
        for msg in messages:
            if msg.role not in ("user", "tool"):
                prepared.append(msg)
                continue

            changed = False
            new_content = []
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.content:
                    new_tool_content = []
                    for item in block.content:
                        if isinstance(item, ImageBlock) and item.data is not None:
                            media_type = item.media_type or "image/png"
                            file_id = await self._upload_image_input(
                                image_bytes=item.data,
                                media_type=media_type,
                                filename=(f"tool-artifact.{self._image_extension(media_type)}"),
                            )
                            new_tool_content.append(ImageBlock(file_id=file_id, detail=item.detail))
                            changed = True
                        else:
                            new_tool_content.append(item)
                    if changed:
                        new_content.append(block.model_copy(update={"content": new_tool_content}))
                    else:
                        new_content.append(block)
                else:
                    new_content.append(block)

            if changed:
                prepared.append(msg.model_copy(update={"content": new_content}))
            else:
                prepared.append(msg)
        return prepared

    async def _serialize_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Serialise framework messages into (instructions, conversation_input).

        Delegates to the centralised OpenAI encoder so that message
        serialisation logic lives in one place.
        """
        prepared = await self._materialize_tool_result_media(messages)
        return _encode_messages(prepared)

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

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        system: str = "",
        response_format: Optional[type["BaseModel"]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        **kwargs: Any,
    ) -> list[ContentBlock]:
        """Generate a single response from OpenAI using Responses API."""
        _, conversation_input = await self._serialize_messages(messages)
        instructions = system

        # ── Unified path: tools + response_format together ────────────────
        # OpenAI Responses API supports both `tools` and `text.format`
        # in the same `responses.create()` call.  The model uses tools
        # when needed and produces schema-conformant text in its final
        # answer.  When a tool-call step is returned, `parsed` stays
        # None; the agent loop continues until the model answers with
        # text, which is then validated against the schema.
        transformed_tools = self._serialize_tools(tools)
        normalized_tool_choice = self._normalize_tool_choice(tool_choice)

        if response_format is not None and transformed_tools:
            text_format = _build_openai_text_format(response_format)

            params: dict[str, Any] = {
                "model": kwargs.get("model", self.model),
                "input": conversation_input,
                "tools": transformed_tools,
                "text": text_format,
            }

            if "temperature" in kwargs:
                params["temperature"] = kwargs["temperature"]
            elif not self.model.startswith("gpt-5"):
                params["temperature"] = self.temperature

            if instructions:
                params["instructions"] = instructions
            if self.max_tokens:
                params["max_output_tokens"] = kwargs.get("max_tokens", self.max_tokens)
            if normalized_tool_choice:
                params["tool_choice"] = normalized_tool_choice

            params.update(
                {
                    k: v
                    for k, v in kwargs.items()
                    if k
                    not in {
                        "model",
                        "input",
                        "instructions",
                        "max_output_tokens",
                        "max_tokens",
                        "temperature",
                        "tools",
                        "text",
                        "tool_choice",
                    }
                }
            )

            response = await self.client.responses.create(**params)

            final_blocks: list[ContentBlock] = []
            final_content_text = getattr(response, "output_text", "") or ""
            if final_content_text:
                final_blocks.append(TextBlock(text=final_content_text))
                
            has_tool_calls = False
            if response.output:
                for item in response.output:
                    if item.type == "function_call":
                        has_tool_calls = True
                        args = json.loads(item.arguments) if isinstance(item.arguments, str) else item.arguments
                        final_blocks.append(
                            ToolUseBlock(
                                call_id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                                tool_name=item.name,
                                arguments=args,
                            )
                        )

            if final_content_text and not has_tool_calls and response_format:
                try:
                    parsed_obj = response_format.model_validate_json(final_content_text)
                    final_blocks.append(DataBlock(data=parsed_obj.model_dump(mode="json")))
                except Exception:
                    logger.debug(
                        f"Failed to parse structured output from text: "
                        f"{final_content_text[:200]}"
                    )

            return final_blocks

        # ── Structured-only path (no tools) ──────────────────────────────
        # if response_format is not None:
        #     import openai
        #     # from ravi.kernel.structured.result import (
        #     #     StructuredOutputError,
        #     #     StructuredOutputResult,
        #     # )

        #     structured_params: dict[str, Any] = {
        #         "model": kwargs.get("model", self.model),
        #         "input": conversation_input,
        #     }
        #     if instructions:
        #         structured_params["instructions"] = instructions
        #     if self.max_tokens:
        #         structured_params["max_output_tokens"] = kwargs.get(
        #             "max_tokens", self.max_tokens
        #         )

        #     # Forward provider-specific structured-output kwargs, but avoid
        #     # duplicating keys we already set above.
        #     structured_params.update(
        #         {
        #             k: v
        #             for k, v in kwargs.items()
        #             if k
        #             not in {
        #                 "model",
        #                 "input",
        #                 "instructions",
        #                 "max_output_tokens",
        #                 "max_tokens",
        #                 "temperature",
        #             }
        #         }
        #     )

        #     try:
        #         response = await self.client.responses.parse(
        #             text_format=response_format,
        #             **structured_params,
        #         )
        #     except openai.APIError as exc:
        #         raise StructuredOutputError(
        #             f"OpenAI API error during structured parse: {exc}"
        #         ) from exc
        #     except Exception as exc:
        #         raise StructuredOutputError(
        #             f"Unexpected error during structured parse: {exc}"
        #         ) from exc

        #     refusal: Optional[str] = None
        #     parsed = getattr(response, "output_parsed", None)
        #     raw_text = getattr(response, "output_text", "") or ""

        #     if response.output:
        #         for item in response.output:
        #             item_refusal = getattr(item, "refusal", None)
        #             if item_refusal:
        #                 refusal = item_refusal
        #                 parsed = None
        #                 break
        #             for block in getattr(item, "content", None) or []:
        #                 if getattr(block, "type", None) == "refusal":
        #                     refusal = getattr(block, "refusal", str(block))
        #                     parsed = None
        #                     break
            
        #     final_blocks: list[ContentBlock] = []
        #     if raw_text:
        #         final_blocks.append(TextBlock(text=raw_text))
            
        #     if refusal:
        #         final_blocks.append(ErrorBlock(error_type="Refusal", message=refusal))
        #     elif parsed:
        #         final_blocks.append(DataBlock(data=parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else parsed))

        #     return final_blocks

        params: dict[str, Any] = {
            "model": self.model,
            "input": conversation_input,
        }

        # GPT-5 models don't support the temperature parameter
        if "temperature" in kwargs:
            params["temperature"] = kwargs["temperature"]
        elif not self.model.startswith("gpt-5"):
            params["temperature"] = self.temperature

        if instructions:
            params["instructions"] = instructions

        if self.max_tokens:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)

        transformed_tools = self._serialize_tools(tools)
        if transformed_tools:
            params["tools"] = transformed_tools
            if normalized_tool_choice:
                params["tool_choice"] = normalized_tool_choice

        # Forward any remaining caller kwargs
        params.update({k: v for k, v in kwargs.items() if k not in params})

        response = await self.client.responses.create(**params)

        final_content_text = getattr(response, "output_text", "") or ""
        final_blocks: list[ContentBlock] = []
        if final_content_text:
            final_blocks.append(TextBlock(text=final_content_text))

        if response.output:
            for item in response.output:
                if item.type == "function_call":
                    args = json.loads(item.arguments) if isinstance(item.arguments, str) else item.arguments
                    final_blocks.append(
                        ToolUseBlock(
                            call_id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                            tool_name=item.name,
                            arguments=args,
                        )
                    )

        return final_blocks

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        *,
        system_instructions: str = "",
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        """Generate a streaming response from OpenAI using Responses API.

        Yields TextDelta, ReasoningDelta, and finally a CompletionEvent.
        """

        _, conversation_input = await self._serialize_messages(messages)
        instructions = system_instructions

        params: dict[str, Any] = {
            "model": self.model,
            "input": conversation_input,
            "stream": True,
        }

        # Only add temperature if explicitly passed or if the model supports it
        # GPT-5 models don't support temperature parameter
        if "temperature" in kwargs:
            params["temperature"] = kwargs["temperature"]
        elif not self.model.startswith("gpt-5"):
            params["temperature"] = self.temperature

        if instructions:
            params["instructions"] = instructions
        if self.max_tokens:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        transformed_tools = self._serialize_tools(tools)
        normalized_tool_choice = self._normalize_tool_choice(tool_choice)
        if transformed_tools:
            params["tools"] = transformed_tools
            if normalized_tool_choice:
                params["tool_choice"] = normalized_tool_choice

        # When response_format is set alongside tools, include text.format
        # so the model produces schema-conformant text in its final answer.
        if response_format is not None and transformed_tools:
            params["text"] = _build_openai_text_format(response_format)

        params.update({k: v for k, v in kwargs.items() if k not in params})

        # Stream and yield deltas, collect final Response object
        final_response = None

        stream = await self.client.responses.create(**params)
        async for event in stream:
            # Yield incremental text deltas
            if isinstance(event, ResponseTextDeltaEvent):
                text = event.delta if hasattr(event, "delta") else ""
                if text:
                    yield TextDelta(text=text)

            # Yield incremental reasoning deltas (o1/o3 models)
            elif isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
                reasoning = event.delta if hasattr(event, "delta") else ""
                if reasoning:
                    yield ReasoningDelta(text=reasoning)

            # Capture final Response object
            elif isinstance(event, ResponseCompletedEvent):
                if hasattr(event, "response"):
                    final_response = event.response

        # Use the Response object to build final message (same as generate())
        if final_response is None:
            logger.error(
                "No ResponseCompletedEvent received from model %s — "
                "the provider may not support the Responses API. "
                "Set use_responses_api=False for this provider.",
                self.model,
            )
            raise RuntimeError(
                f"LLM stream for model '{self.model}' ended without producing "
                f"a completion. The provider may not support the OpenAI "
                f"Responses API. Check server logs for details."
            )
        final_content_text = (
            final_response.output_text if hasattr(final_response, "output_text") else ""
        )
        final_blocks: list[ContentBlock] = []
        if final_content_text:
            final_blocks.append(TextBlock(text=final_content_text))

        has_tool_calls = False
        if final_response.output:
            for item in final_response.output:
                if item.type == "function_call":
                    has_tool_calls = True
                    args = json.loads(item.arguments) if isinstance(item.arguments, str) else item.arguments
                    final_blocks.append(
                        ToolUseBlock(
                            call_id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                            tool_name=item.name,
                            arguments=args,
                        )
                    )

        # Parse structured output from final text when schema is set
        if response_format is not None and final_content_text and not has_tool_calls:
            try:
                parsed_obj = response_format.model_validate_json(final_content_text)
                final_blocks.append(DataBlock(data=parsed_obj.model_dump(mode="json")))
            except Exception:
                logger.debug(
                    f"Stream: failed to parse structured output: "
                    f"{final_content_text[:200]}"
                )

        # Yield final completion
        yield CompletionEvent(content=final_blocks)

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
