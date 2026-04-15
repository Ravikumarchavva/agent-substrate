"""Google Gemini model client implementation."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from google import genai
from google.genai import types as genai_types

from raavan.core.llm.base_client import BaseModelClient
from raavan.core.messages.base_message import BaseClientMessage, UsageStats
from raavan.core.messages.client_messages import (
    AssistantMessage,
    ToolCallMessage,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)


class GeminiClient(BaseModelClient):
    """Google Gemini API client — text and vision.

    Uses the ``google-genai`` SDK for all operations:
      • ``generate`` / ``generate_stream`` → Gemini GenerateContent API
      • ``count_tokens``                   → Gemini CountTokens API

    Audio and image generation are not supported through this client.
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

    # ── Private helpers ──────────────────────────────────────────────────────

    def _serialize_messages(
        self, messages: list[BaseClientMessage]
    ) -> tuple[str, list[genai_types.Content]]:
        """Serialise framework messages into (system_instruction, contents).

        Returns:
            system_instruction: Concatenated system prompt text.
            contents: List of Gemini Content objects.
        """
        system = ""
        contents: list[genai_types.Content] = []

        for msg in messages:
            if msg.role == "system":
                system += f"{msg.content}\n"

            elif msg.role == "user":
                msg_dict = msg.to_dict()
                raw_content = msg_dict.get("content", [])
                parts = self._convert_user_content(raw_content)
                contents.append(genai_types.Content(role="user", parts=parts))

            elif msg.role == "assistant":
                parts: list[genai_types.Part] = []

                # Text content
                if msg.content:
                    for item in msg.content:
                        if isinstance(item, str) and item.strip():
                            parts.append(genai_types.Part(text=item))

                # Function call parts
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        tc_args = tc.arguments
                        if isinstance(tc_args, str):
                            tc_args = json.loads(tc_args)
                        parts.append(
                            genai_types.Part(
                                function_call=genai_types.FunctionCall(
                                    name=tc.name,
                                    args=tc_args,
                                )
                            )
                        )

                if parts:
                    contents.append(genai_types.Content(role="model", parts=parts))

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

                tool_name = getattr(msg, "name", None) or "unknown_tool"
                parts = [
                    genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=tool_name,
                            response={"result": content_str},
                        )
                    )
                ]
                contents.append(genai_types.Content(role="user", parts=parts))

        return system.strip(), contents

    def _convert_user_content(
        self, content: list[dict[str, Any]] | str
    ) -> list[genai_types.Part]:
        """Convert OpenAI Responses API content to Gemini Parts."""
        if isinstance(content, str):
            return [genai_types.Part(text=content)]

        result: list[genai_types.Part] = []
        for block in content:
            if isinstance(block, str):
                result.append(genai_types.Part(text=block))
            elif isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type in ("input_text", "text"):
                    result.append(genai_types.Part(text=block.get("text", "")))
                elif block_type == "input_image":
                    image_url = block.get("image_url", "")
                    if isinstance(image_url, str) and image_url.startswith("data:"):
                        import base64

                        parts = image_url.split(",", 1)
                        media_type = (
                            parts[0].split(":")[1].split(";")[0]
                            if ":" in parts[0]
                            else "image/png"
                        )
                        data = base64.b64decode(parts[1]) if len(parts) > 1 else b""
                        result.append(
                            genai_types.Part(
                                inline_data=genai_types.Blob(
                                    mime_type=media_type, data=data
                                )
                            )
                        )
                    elif isinstance(image_url, str):
                        # URL-based image — use file_data
                        result.append(
                            genai_types.Part(
                                file_data=genai_types.FileData(
                                    file_uri=image_url,
                                    mime_type="image/jpeg",
                                )
                            )
                        )
                else:
                    text = block.get("text", block.get("content", ""))
                    if text:
                        result.append(genai_types.Part(text=str(text)))
            else:
                result.append(genai_types.Part(text=str(block)))

        return result or [genai_types.Part(text="")]

    def _serialize_tools(
        self, tools: Optional[list[dict[str, Any]]]
    ) -> Optional[list[genai_types.Tool]]:
        """Convert tool schemas to Gemini format."""
        if not tools:
            return None

        declarations: list[genai_types.FunctionDeclaration] = []
        for tool in tools:
            name = ""
            description = ""
            parameters: dict[str, Any] = {"type": "OBJECT", "properties": {}}

            # Flattened Responses API format
            if "type" in tool and "name" in tool and "parameters" in tool:
                name = tool["name"]
                description = tool.get("description", "")
                parameters = self._convert_json_schema(tool["parameters"])
            # OpenAI nested format
            elif tool.get("type") == "function" and "function" in tool:
                fn = tool["function"]
                name = fn.get("name", "")
                description = fn.get("description", "")
                parameters = self._convert_json_schema(
                    fn.get("parameters", {"type": "object", "properties": {}})
                )
            # MCP format
            elif "name" in tool and "inputSchema" in tool:
                name = tool["name"]
                description = tool.get("description", "")
                parameters = self._convert_json_schema(tool["inputSchema"])
            # Generic named tool
            elif "name" in tool:
                name = tool["name"]
                description = tool.get("description", "")
                raw = (
                    tool.get("parameters")
                    or tool.get("inputSchema")
                    or {"type": "object", "properties": {}}
                )
                parameters = self._convert_json_schema(raw)

            if name:
                declarations.append(
                    genai_types.FunctionDeclaration(
                        name=name,
                        description=description,
                        parameters=parameters,
                    )
                )

        return (
            [genai_types.Tool(function_declarations=declarations)]
            if declarations
            else None
        )

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

    # ── Text / Vision (required) ─────────────────────────────────────────────

    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> Any:
        """Generate a single response from Gemini using GenerateContent API."""
        system_instruction, contents = self._serialize_messages(messages)

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
        if gemini_tools:
            config["tools"] = gemini_tools

        if response_format is not None and not tools:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_format

        response = await self.client.aio.models.generate_content(
            model=kwargs.get("model", self.model),
            contents=contents,
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
                                name=fc.name,
                                arguments=dict(fc.args) if fc.args else {},
                            )
                        )

        final_text = "\n".join(text_parts) if text_parts else ""
        final_content: Optional[list[Any]] = [final_text] if final_text else None

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
        tools: Optional[list[dict]] = None,
        *,
        response_format: Optional[type["BaseModel"]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Generate a streaming response from Gemini.

        Yields TextDeltaChunk objects, then a final CompletionChunk.
        """
        from raavan.core.messages._types import TextDeltaChunk, CompletionChunk

        system_instruction, contents = self._serialize_messages(messages)

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
        if gemini_tools:
            config["tools"] = gemini_tools

        # Accumulate for final message
        text_parts: list[str] = []
        tool_calls_obj: Optional[list[ToolCallMessage]] = None
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        async for chunk in await self.client.aio.models.generate_content_stream(
            model=kwargs.get("model", self.model),
            contents=contents,
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
                                    name=fc.name,
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
        final_content: Optional[list[Any]] = [final_text] if final_text else None

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
                contents=contents,
            )
            return result.total_tokens
        except Exception:
            # Fallback: rough estimate
            total_chars = sum(len(str(c)) for c in contents)
            return total_chars // 4
