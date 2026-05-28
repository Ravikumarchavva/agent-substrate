"""Google Gemini model client implementation."""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator, Optional, cast

from google import genai
from google.genai import types as genai_types

from ravi.kernel.llm.base_client import (
    BaseModelClient,
    GenerateResult,
    ModelStreamEvent,
)
from ravi.kernel.messages.base_message import BaseClientMessage, UsageStats
from ravi.kernel.messages._types import MediaType
from ravi.kernel.messages.client_messages import (
    AssistantMessage,
    ToolCallMessage,
)
from ravi.kernel.messages.encoders.gemini import (
    encode_messages as _encode_messages,
    encode_tools as _encode_tools,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = setup_logging()


class GeminiClient(BaseModelClient):
    """Google Gemini API client — text and vision.

    Uses the ``google-genai`` SDK for all operations:
      • ``generate`` / ``generate_stream`` → Gemini GenerateContent API
      • ``count_tokens``                   → Gemini CountTokens API

    Audio transcription and live audio are not supported through this client.
    Text-to-speech is supported via Gemini TTS preview models.
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        super().__init__(model, temperature, max_tokens, **kwargs)
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key)

    @property
    def supports_audio(self) -> bool:
        return True

    # ── Private helpers ──────────────────────────────────────────────────────

    # ── Private helpers — delegates to ``core.messages.encoders.gemini`` ─────

    def _serialize_messages(
        self, messages: list[BaseClientMessage]
    ) -> tuple[str, list[genai_types.Content]]:
        """Serialise framework messages into (system_instruction, contents).

        Delegates to the centralised Gemini encoder.
        """
        return _encode_messages(messages)

    def _serialize_tools(
        self, tools: Optional[list[dict[str, Any]]]
    ) -> Optional[list[genai_types.Tool]]:
        """Convert tool schemas to Gemini format.

        Delegates to the centralised Gemini encoder with JSON Schema
        conversion applied.
        """
        return _encode_tools(tools, convert_schema=self._convert_json_schema)

    def _convert_json_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Convert JSON Schema to Gemini-compatible schema format.

        Gemini uses uppercase type names and a slightly different schema format.
        """
        if not schema:
            return {"type": "OBJECT", "properties": {}}

        result: dict[str, Any] = {}

        json_type = schema.get("type", "object")
        type_map = {
            "object": "OBJECT",
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
        }
        result["type"] = type_map.get(json_type, "STRING")

        if "description" in schema:
            result["description"] = schema["description"]

        if "properties" in schema:
            result["properties"] = {
                k: self._convert_json_schema(v) for k, v in schema["properties"].items()
            }

        if "required" in schema:
            result["required"] = schema["required"]

        if "items" in schema:
            result["items"] = self._convert_json_schema(schema["items"])

        if "enum" in schema:
            result["enum"] = schema["enum"]

        return result

    @staticmethod
    def _build_tool_config(
        tool_choice: Optional[str | dict[str, Any]],
    ) -> Optional[genai_types.ToolConfig]:
        """Translate tool forcing into Gemini GenerateContentConfig shape."""
        if not tool_choice:
            return None

        if isinstance(tool_choice, str):
            if tool_choice == "auto":
                function_calling_config = genai_types.FunctionCallingConfig(
                    mode=genai_types.FunctionCallingConfigMode.AUTO
                )
            elif tool_choice == "required":
                function_calling_config = genai_types.FunctionCallingConfig(
                    mode=genai_types.FunctionCallingConfigMode.ANY
                )
            elif tool_choice == "none":
                function_calling_config = genai_types.FunctionCallingConfig(
                    mode=genai_types.FunctionCallingConfigMode.NONE
                )
            else:
                function_calling_config = genai_types.FunctionCallingConfig(
                    mode=genai_types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=[tool_choice],
                )
            return genai_types.ToolConfig(
                function_calling_config=function_calling_config
            )

        if (
            "function_calling_config" in tool_choice
            or "functionCallingConfig" in tool_choice
        ):
            return genai_types.ToolConfig.model_validate(tool_choice)

        return genai_types.ToolConfig(
            function_calling_config=genai_types.FunctionCallingConfig.model_validate(
                tool_choice
            )
        )

    # ── Text / Vision (required) ─────────────────────────────────────────────

    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        system_instructions: str = "",
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> GenerateResult:
        """Generate a single response from Gemini using GenerateContent API."""
        _, contents = self._serialize_messages(messages)
        system_instruction = system_instructions

        config: dict[str, Any] = {}

        if "temperature" in kwargs:
            config["temperature"] = kwargs["temperature"]
        else:
            config["temperature"] = self.temperature

        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens:
            config["max_output_tokens"] = max_tokens

        if system_instruction:
            config["system_instruction"] = system_instruction

        gemini_tools = self._serialize_tools(tools)
        normalized_tool_config = self._build_tool_config(tool_choice)
        if gemini_tools:
            config["tools"] = gemini_tools
            if normalized_tool_config is not None:
                config["tool_config"] = normalized_tool_config

        if response_format is not None and not tools:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_format

        response = await self.client.aio.models.generate_content(
            model=kwargs.get("model", self.model),
            contents=cast(Any, contents),
            config=genai_types.GenerateContentConfig(**config),
        )

        # Extract text and tool calls
        text_parts: list[str] = []
        tool_calls_obj: Optional[list[ToolCallMessage]] = None

        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.text:
                        text_parts.append(part.text)
                    elif part.function_call:
                        if tool_calls_obj is None:
                            tool_calls_obj = []
                        fc = part.function_call
                        tool_calls_obj.append(
                            ToolCallMessage(
                                name=fc.name or "gemini_tool",
                                arguments=dict(fc.args) if fc.args else {},
                            )
                        )

        final_text = "\n".join(text_parts) if text_parts else ""
        final_content: Optional[list[MediaType]] = [final_text] if final_text else None

        # Usage
        usage_dict = None
        if response.usage_metadata:
            um = response.usage_metadata
            usage_dict = UsageStats(
                prompt_tokens=um.prompt_token_count or 0,
                completion_tokens=um.candidates_token_count or 0,
                total_tokens=um.total_token_count or 0,
            )

        finish_reason = "stop"
        if tool_calls_obj:
            finish_reason = "tool_calls"

        msg = AssistantMessage(
            role="assistant",
            content=final_content,
            tool_calls=tool_calls_obj,
            usage=usage_dict,
            finish_reason=finish_reason,
        )

        # Structured output parsing
        if response_format is not None and final_text and not tool_calls_obj:
            try:
                msg.parsed = response_format.model_validate_json(final_text)
            except Exception:
                logger.debug(
                    "Failed to parse structured output from Gemini: %s",
                    final_text[:200],
                )

        return msg

    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        system_instructions: str = "",
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Generate a streaming response from Gemini.

        Yields TextDeltaChunk objects, then a final CompletionChunk.
        """
        from ravi.kernel.messages._types import TextDeltaChunk, CompletionChunk

        _, contents = self._serialize_messages(messages)
        system_instruction = system_instructions

        config: dict[str, Any] = {}

        if "temperature" in kwargs:
            config["temperature"] = kwargs["temperature"]
        else:
            config["temperature"] = self.temperature

        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens:
            config["max_output_tokens"] = max_tokens

        if system_instruction:
            config["system_instruction"] = system_instruction

        gemini_tools = self._serialize_tools(tools)
        normalized_tool_config = self._build_tool_config(tool_choice)
        if gemini_tools:
            config["tools"] = gemini_tools
            if normalized_tool_config is not None:
                config["tool_config"] = normalized_tool_config

        # Accumulate for final message
        text_parts: list[str] = []
        tool_calls_obj: Optional[list[ToolCallMessage]] = None
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        async for chunk in await self.client.aio.models.generate_content_stream(
            model=kwargs.get("model", self.model),
            contents=cast(Any, contents),
            config=genai_types.GenerateContentConfig(**config),
        ):
            if chunk.candidates:
                candidate = chunk.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            text_parts.append(part.text)
                            yield TextDeltaChunk(text=part.text)
                        elif part.function_call:
                            if tool_calls_obj is None:
                                tool_calls_obj = []
                            fc = part.function_call
                            tool_calls_obj.append(
                                ToolCallMessage(
                                    name=fc.name or "gemini_tool",
                                    arguments=dict(fc.args) if fc.args else {},
                                )
                            )

            if chunk.usage_metadata:
                um = chunk.usage_metadata
                prompt_tokens = um.prompt_token_count or 0
                completion_tokens = um.candidates_token_count or 0
                total_tokens = um.total_token_count or 0

        # Build final message
        final_text = "".join(text_parts) if text_parts else ""
        final_content: Optional[list[MediaType]] = [final_text] if final_text else None

        finish_reason = "stop"
        if tool_calls_obj:
            finish_reason = "tool_calls"

        usage_dict = UsageStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

        final_message = AssistantMessage(
            role="assistant",
            content=final_content,
            tool_calls=tool_calls_obj,
            usage=usage_dict,
            finish_reason=finish_reason,
        )

        # Structured output parsing
        if response_format is not None and final_text and not tool_calls_obj:
            try:
                final_message.parsed = response_format.model_validate_json(final_text)
            except Exception:
                logger.debug(
                    "Stream: failed to parse structured output from Gemini: %s",
                    final_text[:200],
                )

        yield CompletionChunk(message=final_message)

    async def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        """Count tokens using Gemini's CountTokens API."""
        _, contents = self._serialize_messages(messages)
        try:
            result = await self.client.aio.models.count_tokens(
                model=self.model,
                contents=cast(Any, contents),
            )
            return int(result.total_tokens or 0)
        except Exception:
            # Fallback: rough estimate
            total_chars = sum(len(str(c)) for c in contents)
            return total_chars // 4

    async def stream_tts(
        self,
        *,
        text: str,
        voice: str = "",
        model: str = "",
        response_format: str = "",
        instructions: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """Synthesize speech with Gemini TTS preview models.

        Gemini TTS currently returns a single WAV payload rather than a true
        incremental stream, so this async generator yields one chunk.
        """
        effective_model = model or self.model
        effective_voice = voice or "Kore"
        effective_format = (response_format or "wav").lower()
        if effective_format != "wav":
            raise ValueError("Gemini TTS currently supports WAV output only")

        prompt_parts = []
        if instructions and instructions.strip():
            prompt_parts.append(instructions.strip())
        prompt_parts.append("Speak the following text verbatim.")
        prompt_parts.append(text)
        prompt_text = "\n\n".join(prompt_parts)

        response = await self.client.aio.models.generate_content(
            model=effective_model,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=prompt_text)],
                )
            ],
            config=genai_types.GenerateContentConfig(
                response_modalities=[genai_types.Modality.AUDIO],
                speech_config=genai_types.SpeechConfig(
                    voice_config=genai_types.VoiceConfig(
                        prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                            voice_name=effective_voice
                        )
                    )
                ),
            ),
        )

        for candidate in response.candidates or []:
            content = getattr(candidate, "content", None)
            if not content or not content.parts:
                continue
            for part in content.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    yield inline_data.data
                    return

        raise ValueError("Gemini TTS returned no audio data")
