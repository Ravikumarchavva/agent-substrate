"""OpenAI model client implementation."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator, Optional
import json
from openai import AsyncOpenAI
from openai.types.responses.response_completed_event import ResponseCompletedEvent
from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
from openai.types.responses.response_reasoning_summary_text_delta_event import (
    ResponseReasoningSummaryTextDeltaEvent,
)
import tiktoken

from raavan.core.messages.client_messages import (
    ToolCallMessage,
    AssistantMessage,
)

from raavan.core.llm.base_client import BaseModelClient
from raavan.core.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

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


class OpenAIClient(BaseModelClient):
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
        model: str = "gpt-5-mini",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        *,
        default_stt_model: str = "whisper-1",
        default_tts_model: str = "gpt-4o-mini-tts",
        default_voice: str = "coral",
        default_tts_format: str = "mp3",
        realtime_model: str = "gpt-4o-realtime-preview-2024-12-17",
        **kwargs,
    ):
        super().__init__(model, temperature, max_tokens, **kwargs)
        self.api_key = api_key  # stored so PipelineRunner can build sibling clients
        self.client = AsyncOpenAI(api_key=api_key)
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

    def _messages_to_openai_format(
        self, messages: list[BaseClientMessage]
    ) -> list[dict]:
        """Convert framework messages to OpenAI API format."""
        return [msg.to_dict() for msg in messages]

    def _tools_to_openai_format(
        self, tools: Optional[list[dict]]
    ) -> Optional[list[dict]]:
        """Convert tools to OpenAI function calling format."""
        if not tools:
            return None
        return tools

    # ------------------------------------------------------------------
    # Private helpers — shared serialisation logic
    # ------------------------------------------------------------------

    def _serialize_messages(
        self, messages: list[BaseClientMessage]
    ) -> tuple[str, list[dict]]:
        """Serialise framework messages into (instructions, conversation_input).

        Returns:
            instructions: Concatenated system prompt text for the Responses API.
            conversation_input: List of Responses-API input items.
        """
        instructions = ""
        conversation_input: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                instructions += f"{msg.content}\n"
            elif msg.role == "user":
                msg_dict = msg.to_dict()
                conversation_input.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": msg_dict.get("content", []),
                    }
                )
            elif msg.role == "assistant":
                msg_dict = msg.to_dict()
                if msg.content:
                    serialized_content = msg_dict.get("content", [])
                    if serialized_content:
                        conversation_input.append(
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": serialized_content,
                            }
                        )
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        if not (hasattr(tc, "name") and hasattr(tc, "arguments")):
                            continue
                        tc_args = tc.arguments
                        if isinstance(tc_args, dict):
                            tc_args = json.dumps(tc_args)
                        conversation_input.append(
                            {
                                "type": "function_call",
                                "call_id": tc.id,
                                "name": tc.name,
                                "arguments": tc_args,
                            }
                        )
            elif msg.role in ("tool_response", "tool"):
                content_str = ""
                if hasattr(msg, "content") and msg.content:
                    if isinstance(msg.content, list):
                        content_str = "\n".join(
                            block.get("text", "")
                            for block in msg.content
                            if isinstance(block, dict) and block.get("type") == "text"
                        )
                    elif isinstance(msg.content, str):
                        content_str = msg.content
                conversation_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": getattr(msg, "tool_call_id", None),
                        "output": content_str,
                    }
                )

        return instructions.strip(), conversation_input

    def _serialize_tools(self, tools: Optional[list[dict]]) -> Optional[list[dict]]:
        """Normalise tool dicts to Responses API flattened format.

        Accepts OpenAI nested format, MCP format, or already-flattened format.
        Returns None when *tools* is falsy.
        """
        if not tools:
            return None

        result: list[dict] = []
        for tool in tools:
            # Already flattened Responses-API format
            if "type" in tool and "name" in tool and "parameters" in tool:
                result.append(tool)
            # OpenAI nested Chat Completions format
            elif tool.get("type") == "function" and "function" in tool:
                fn = tool["function"]
                result.append(
                    {
                        "type": "function",
                        "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                )
            # MCP format with inputSchema
            elif "name" in tool and "inputSchema" in tool:
                result.append(
                    {
                        "type": "function",
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool["inputSchema"],
                    }
                )
            # Generic named tool — best-effort
            elif "name" in tool:
                result.append(
                    {
                        "type": "function",
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": (
                            tool.get("parameters")
                            or tool.get("inputSchema")
                            or {"type": "object", "properties": {}}
                        ),
                    }
                )
            else:
                # Unknown format — pass through and let the API handle it
                result.append(tool)

        return result

    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        *,
        response_format: Optional[type["BaseModel"]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """Generate a single response from OpenAI using Responses API."""
        instructions, conversation_input = self._serialize_messages(messages)

        # ── Unified path: tools + response_format together ────────────────
        # OpenAI Responses API supports both `tools` and `text.format`
        # in the same `responses.create()` call.  The model uses tools
        # when needed and produces schema-conformant text in its final
        # answer.  When a tool-call step is returned, `parsed` stays
        # None; the agent loop continues until the model answers with
        # text, which is then validated against the schema.
        transformed_tools = self._serialize_tools(tools)

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
            if tool_choice:
                params["tool_choice"] = tool_choice

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

            # Extract tool calls
            tool_calls_obj = None
            if response.output:
                for item in response.output:
                    if item.type == "function_call":
                        if tool_calls_obj is None:
                            tool_calls_obj = []
                        tool_calls_obj.append(
                            ToolCallMessage(
                                id=getattr(item, "call_id", "")
                                or getattr(item, "id", ""),
                                name=item.name,
                                arguments=json.loads(item.arguments)
                                if isinstance(item.arguments, str)
                                else item.arguments,
                            )
                        )

            # Extract text content
            final_content_text = getattr(response, "output_text", "") or ""
            final_content: Optional[list[Any]] = (
                [final_content_text] if final_content_text else None
            )

            # Try to parse structured output from text (only on final text answer)
            parsed_obj = None
            if final_content_text and not tool_calls_obj:
                try:
                    parsed_obj = response_format.model_validate_json(final_content_text)
                except Exception:
                    logger.debug(
                        f"Failed to parse structured output from text: "
                        f"{final_content_text[:200]}"
                    )

            usage_dict = None
            if getattr(response, "usage", None):
                from raavan.core.messages.base_message import UsageStats

                usage_dict = UsageStats(
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.total_tokens,
                )

            finish_reason = "stop"
            if tool_calls_obj:
                finish_reason = "tool_calls"
            elif getattr(response, "finish_reason", None):
                finish_reason = getattr(response, "finish_reason", "stop")

            return AssistantMessage(
                role="assistant",
                content=final_content,
                tool_calls=tool_calls_obj,
                usage=usage_dict,
                finish_reason=finish_reason,
                parsed=parsed_obj,
            )

        # ── Structured-only path (no tools) ──────────────────────────────
        if response_format is not None:
            import openai
            from raavan.core.structured.result import (
                StructuredOutputError,
                StructuredOutputResult,
            )

            structured_params: dict[str, Any] = {
                "model": kwargs.get("model", self.model),
                "input": conversation_input,
            }
            if instructions:
                structured_params["instructions"] = instructions
            if self.max_tokens:
                structured_params["max_output_tokens"] = kwargs.get(
                    "max_tokens", self.max_tokens
                )

            # Forward provider-specific structured-output kwargs, but avoid
            # duplicating keys we already set above.
            structured_params.update(
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
                    }
                }
            )

            try:
                response = await self.client.responses.parse(
                    text_format=response_format,
                    **structured_params,
                )
            except openai.APIError as exc:
                raise StructuredOutputError(
                    f"OpenAI API error during structured parse: {exc}"
                ) from exc
            except Exception as exc:
                raise StructuredOutputError(
                    f"Unexpected error during structured parse: {exc}"
                ) from exc

            refusal: Optional[str] = None
            parsed = getattr(response, "output_parsed", None)
            raw_text = getattr(response, "output_text", "") or ""

            if response.output:
                for item in response.output:
                    item_refusal = getattr(item, "refusal", None)
                    if item_refusal:
                        refusal = item_refusal
                        parsed = None
                        break
                    for block in getattr(item, "content", None) or []:
                        if getattr(block, "type", None) == "refusal":
                            refusal = getattr(block, "refusal", str(block))
                            parsed = None
                            break

            return StructuredOutputResult(
                parsed=parsed,
                raw_text=raw_text,
                refusal=refusal,
                model=self.model,
            )

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
            if tool_choice:
                params["tool_choice"] = tool_choice

        # Forward any remaining caller kwargs
        params.update({k: v for k, v in kwargs.items() if k not in params})

        response = await self.client.responses.create(**params)

        final_content_text = getattr(response, "output_text", "") or ""
        final_content: Optional[list[Any]] = (
            [final_content_text] if final_content_text else None
        )

        tool_calls_obj = None
        if response.output:
            for item in response.output:
                if item.type == "function_call":
                    if tool_calls_obj is None:
                        tool_calls_obj = []
                    tool_calls_obj.append(
                        ToolCallMessage(
                            id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                            name=item.name,
                            arguments=json.loads(item.arguments)
                            if isinstance(item.arguments, str)
                            else item.arguments,
                        )
                    )

        usage_dict = None
        if getattr(response, "usage", None):
            from raavan.core.messages.base_message import UsageStats

            usage_dict = UsageStats(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            )

        finish_reason = "stop"
        if tool_calls_obj:
            finish_reason = "tool_calls"
        elif getattr(response, "finish_reason", None):
            finish_reason = getattr(response, "finish_reason", "stop")

        return AssistantMessage(
            role="assistant",
            content=final_content,
            tool_calls=tool_calls_obj,
            usage=usage_dict,
            finish_reason=finish_reason,
        )

    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
        *,
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs,
    ) -> AsyncIterator[Any]:
        """Generate a streaming response from OpenAI using Responses API.

        Yields StreamChunk objects:
        - TextDeltaChunk: Incremental text content
        - ReasoningDeltaChunk: Incremental reasoning (o1/o3 models)
        - CompletionChunk: Final response with complete AssistantMessage (includes tool calls)
        """
        from raavan.core.messages._types import (
            TextDeltaChunk,
            ReasoningDeltaChunk,
            CompletionChunk,
        )

        instructions, conversation_input = self._serialize_messages(messages)

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
        if transformed_tools:
            params["tools"] = transformed_tools
            if tool_choice:
                params["tool_choice"] = tool_choice

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
                    yield TextDeltaChunk(text=text)

            # Yield incremental reasoning deltas (o1/o3 models)
            elif isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
                reasoning = event.delta if hasattr(event, "delta") else ""
                if reasoning:
                    yield ReasoningDeltaChunk(text=reasoning)

            # Capture final Response object
            elif isinstance(event, ResponseCompletedEvent):
                if hasattr(event, "response"):
                    final_response = event.response

        # Use the Response object to build final message (same as generate())
        if final_response is None:
            # Fallback if no completion event
            final_message = AssistantMessage(
                role="assistant",
                content=None,
                tool_calls=None,
                usage=None,
                finish_reason="error",
            )
        else:
            # Extract content text
            final_content_text = (
                final_response.output_text
                if hasattr(final_response, "output_text")
                else ""
            )
            final_content: Optional[list[Any]] = (
                [final_content_text] if final_content_text else None
            )

            # Extract tool calls from output
            tool_calls_obj = None
            if final_response.output:
                for item in final_response.output:
                    if item.type == "function_call":
                        if tool_calls_obj is None:
                            tool_calls_obj = []
                        tool_calls_obj.append(
                            ToolCallMessage(
                                id=getattr(item, "call_id", "")
                                or getattr(item, "id", ""),
                                name=item.name,
                                arguments=json.loads(item.arguments)
                                if isinstance(item.arguments, str)
                                else item.arguments,
                            )
                        )

            # Extract usage
            usage_dict = None
            if hasattr(final_response, "usage") and final_response.usage:
                from raavan.core.messages.base_message import UsageStats

                usage_dict = UsageStats(
                    prompt_tokens=final_response.usage.input_tokens,
                    completion_tokens=final_response.usage.output_tokens,
                    total_tokens=final_response.usage.total_tokens,
                )

            # Determine finish reason
            finish_reason = "stop"
            if tool_calls_obj:
                finish_reason = "tool_calls"
            elif getattr(final_response, "finish_reason", None):
                finish_reason = getattr(final_response, "finish_reason", "stop")

            final_message = AssistantMessage(
                role="assistant",
                content=final_content,
                tool_calls=tool_calls_obj,
                usage=usage_dict,
                finish_reason=finish_reason,
            )

            # Parse structured output from final text when schema is set
            if (
                response_format is not None
                and final_content_text
                and not tool_calls_obj
            ):
                try:
                    final_message.parsed = response_format.model_validate_json(
                        final_content_text
                    )
                except Exception:
                    logger.debug(
                        f"Stream: failed to parse structured output: "
                        f"{final_content_text[:200]}"
                    )

        # Yield final completion
        yield CompletionChunk(message=final_message)

    async def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        """Count tokens using tiktoken."""
        encoding = self._get_encoding()
        num_tokens = 0

        for message in messages:
            # Every message follows <im_start>{role/name}\n{content}<im_end>\n
            num_tokens += 4
            msg_dict = message.to_dict()

            for key, value in msg_dict.items():
                if isinstance(value, str):
                    num_tokens += len(encoding.encode(value))
                elif key == "tool_calls" and value:
                    # Approximate tool calls
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
