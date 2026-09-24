"""Google Gemini model client implementation."""

from __future__ import annotations
from substrate.logger import setup_logging

from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator, Optional, cast

from google import genai
from google.genai import types as genai_types

import uuid
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
    DataBlock,
    ReasoningBlock,
    TextBlock,
    ToolUseBlock,
)
from substrate.kernel.messaging.stream import TextDelta, ReasoningDelta, CompletionEvent
from substrate.integrations.llm.encoders.gemini import (
    encode_messages as _encode_messages,
    encode_tools as _encode_tools,
)

if TYPE_CHECKING:
    pass

logger = setup_logging()


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


class GeminiClient(LLMClient):
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
        capabilities: Optional[ModelCapabilities] = None,
        **kwargs: Any,
    ):
        self.model = model
        self.capabilities = capabilities or resolve_capabilities(model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key
        # google-genai doesn't retry unless asked; the openai/anthropic SDKs do by default.
        self.client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                retry_options=genai_types.HttpRetryOptions(attempts=3)
            ),
        )

    @property
    def supports_audio(self) -> bool:
        return True

    # ── Private helpers ──────────────────────────────────────────────────────

    # ── Private helpers — delegates to ``core.messages.encoders.gemini`` ─────

    def _serialize_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[str, list[genai_types.Content]]:
        """Serialise framework messages into (system_instruction, contents).

        Delegates to the centralised Gemini encoder.
        """
        return _encode_messages(
            fit_to_capabilities(messages, self.capabilities),
            # Gemini 3+ accepts media inside a function response itself.
            native_tool_media=self.model.startswith("gemini-3"),
        )

    @staticmethod
    def _usage(u: Any) -> Usage:
        """``candidates_token_count`` excludes thought tokens, which are billed
        as output — fold them in."""
        if u is None:
            return Usage()
        thoughts = getattr(u, "thoughts_token_count", 0) or 0
        return Usage(
            input_tokens=getattr(u, "prompt_token_count", 0) or 0,
            cached_tokens=getattr(u, "cached_content_token_count", 0) or 0,
            output_tokens=(getattr(u, "candidates_token_count", 0) or 0) + thoughts,
            reasoning_tokens=thoughts,
        )

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

    _EFFORT_BUDGET = {
        ReasoningEffort.LOW: 1_024,
        ReasoningEffort.MEDIUM: 8_192,
        ReasoningEffort.HIGH: 24_576,
    }

    def _thinking_config(
        self, options: GenerationOptions, *, has_tools: bool
    ) -> genai_types.ThinkingConfig | None:
        """Thinking-enabled Gemini models attach an opaque thought_signature to
        function_call parts and require it back verbatim when that call is
        replayed as history; this engine's persisted history doesn't carry it,
        so a replayed call trips a 400 ("Function call is missing a
        thought_signature"). Turns that offer tools therefore run with thinking
        off, whatever ``options.reasoning`` says — see
        ai.google.dev/gemini-api/docs/thought-signatures."""
        if has_tools:
            if options.reasoning not in (None, ReasoningEffort.OFF):
                logger.info(
                    "Gemini reasoning=%s ignored on a tool-calling turn "
                    "(thought signatures are not persisted)",
                    options.reasoning,
                )
            return genai_types.ThinkingConfig(thinking_budget=0)
        effort = options.reasoning
        if effort is None or not self.capabilities.supports_reasoning:
            return None
        if effort == ReasoningEffort.OFF:
            return genai_types.ThinkingConfig(thinking_budget=0)
        return genai_types.ThinkingConfig(
            thinking_budget=self._EFFORT_BUDGET[effort], include_thoughts=True
        )

    def _build_request(
        self, messages: list[ChatMessage], options: GenerationOptions
    ) -> tuple[list[genai_types.Content], genai_types.GenerateContentConfig]:
        _, contents = self._serialize_messages(messages)
        config: dict[str, Any] = {
            "temperature": (
                options.temperature if options.temperature is not None else self.temperature
            )
        }
        max_tokens = options.max_tokens or self.max_tokens
        if max_tokens:
            config["max_output_tokens"] = max_tokens
        if options.system_instructions:
            config["system_instruction"] = options.system_instructions

        tool_dicts = _tools_from_options(options)
        gemini_tools = self._serialize_tools(tool_dicts)
        if gemini_tools:
            config["tools"] = gemini_tools
            tool_config = self._build_tool_config(options.tool_choice)
            if tool_config is not None:
                config["tool_config"] = tool_config
        thinking = self._thinking_config(options, has_tools=bool(gemini_tools))
        if thinking is not None:
            config["thinking_config"] = thinking

        if options.response_format is not None and not tool_dicts:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = options.response_format
        return contents, genai_types.GenerateContentConfig(**config)

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: RunMeta | None = None,
    ) -> LLMResponse:
        """Generate a single response from Gemini using GenerateContent API."""
        response_format = options.response_format
        contents, config = self._build_request(messages, options)

        response = await self.client.aio.models.generate_content(
            model=self.model, contents=cast(Any, contents), config=config
        )

        # Extract text and tool calls
        final_blocks: list[ContentBlock] = []
        has_tool_calls = False

        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.text and part.thought:
                        # Thought summary (include_thoughts): reasoning, not answer.
                        final_blocks.append(ReasoningBlock(text=part.text))
                    elif part.text:
                        final_blocks.append(TextBlock(text=part.text))
                    elif part.function_call:
                        has_tool_calls = True
                        fc = part.function_call
                        final_blocks.append(
                            ToolUseBlock(
                                call_id=str(uuid.uuid4()),
                                tool_name=fc.name or "gemini_tool",
                                arguments=dict(fc.args) if fc.args else {},
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
                        "Failed to parse structured output from Gemini: %s",
                        final_text[:200],
                    )

        usage = self._usage(response.usage_metadata)

        return LLMResponse(content=final_blocks, usage=usage)

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
        ctx: RunMeta | None = None,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        response_format = options.response_format
        contents, config = self._build_request(messages, options)

        # Accumulate for final message
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        collected_tool_calls: list[ToolUseBlock] = []
        usage_metadata: Any = None

        async for chunk in await self.client.aio.models.generate_content_stream(
            model=self.model, contents=cast(Any, contents), config=config
        ):
            if chunk.usage_metadata:
                usage_metadata = chunk.usage_metadata  # cumulative; the last one wins
            if chunk.candidates:
                candidate = chunk.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text and part.thought:
                            reasoning_parts.append(part.text)
                            yield ReasoningDelta(text=part.text)
                        elif part.text:
                            text_parts.append(part.text)
                            yield TextDelta(text=part.text)
                        elif part.function_call:
                            fc = part.function_call
                            collected_tool_calls.append(
                                ToolUseBlock(
                                    call_id=str(uuid.uuid4()),
                                    tool_name=fc.name or "gemini_tool",
                                    arguments=dict(fc.args) if fc.args else {},
                                )
                            )

        # Build final message
        final_text = "".join(text_parts) if text_parts else ""
        final_blocks: list[ContentBlock] = []

        if reasoning_parts:
            final_blocks.append(ReasoningBlock(text="".join(reasoning_parts)))
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
                    "Stream: failed to parse structured output from Gemini: %s",
                    final_text[:200],
                )

        yield CompletionEvent(
            content=final_blocks, usage=self._usage(usage_metadata)
        )

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
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
