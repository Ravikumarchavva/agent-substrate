"""OpenAI model client implementation."""
from typing import Any, AsyncIterator, Optional
import json
from openai import AsyncOpenAI
import tiktoken

from agent_framework.messages.client_messages import ToolExecutionResultMessage, ToolCallMessage, AssistantMessage, SystemMessage, UserMessage

from ..base_client import BaseModelClient, ModelResponse
from agent_framework.messages.base_message import BaseClientMessage

class OpenAIClient(BaseModelClient):
    """OpenAI API client with support for chat completions and tool calling."""
    
    def __init__(
        self,
        model: str = "gpt-5-mini",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        super().__init__(model, temperature, max_tokens, **kwargs)
        self.client = AsyncOpenAI(api_key=api_key)
        self._encoding = None
    
    def _get_encoding(self):
        """Lazy load tiktoken encoding."""
        if self._encoding is None:
            try:
                self._encoding = tiktoken.encoding_for_model(self.model)
            except KeyError:
                self._encoding = tiktoken.get_encoding("cl100k_base")
        return self._encoding
    
    def _messages_to_openai_format(self, messages: list[BaseClientMessage]) -> list[dict]:
        """Convert framework messages to OpenAI API format."""
        return [msg.to_dict() for msg in messages]
    
    def _tools_to_openai_format(self, tools: Optional[list[dict]]) -> Optional[list[dict]]:
        """Convert tools to OpenAI function calling format."""
        if not tools:
            return None
        return tools
    
    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
        **kwargs
    ) -> ModelResponse:
        """Generate a single response from OpenAI using Responses API."""
        # Separate system instructions from other messages
        instructions = ""
        conversation_input = []
        
        for msg in messages:
            if msg.role == "system":
                # For Responses API, instructions are typically passed as a separate param
                # But if we have multiple, we can append them.
                instructions += f"{msg.content}\n"
            elif msg.role == "user":
                conversation_input.append({
                    "type": "message",
                    "role": "user",
                    "content": msg.content
                })
            elif msg.role == "assistant":
                # Assistant message might have content OR tool_calls or both
                if msg.content:
                    conversation_input.append({
                        "type": "message",
                        "role": "assistant",
                        "content": msg.content
                    })
                
                # Check for tool_calls (framework AssistantMessage or ModelResponse)
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        conversation_input.append({
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.function["name"],
                            "arguments": tc.function["arguments"]
                        })
            elif msg.role == "tool":
                # ToolMessage maps to function_call_output
                conversation_input.append({
                    "type": "function_call_output",
                    "call_id": getattr(msg, "tool_call_id", None),
                    "output": msg.content
                })

        params = {
            "model": self.model,
            "input": conversation_input,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        if instructions:
             params["instructions"] = instructions.strip()
        
        if self.max_tokens:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        
        if tools:
            # Transform tools to Responses API format (flattened)
            # The Responses API expects { "type": "function", "name": "...", "description": "...", "parameters": ... }
            # whereas the previous format was { "type": "function", "function": { "name": "...", ... } }
            transformed_tools = []
            for tool in tools:
                if tool.get("type") == "function" and "function" in tool:
                    fn_def = tool["function"]
                    new_tool = {
                        "type": "function",
                        "name": fn_def.get("name"),
                        "description": fn_def.get("description"),
                        "parameters": fn_def.get("parameters"),
                    }
                    transformed_tools.append(new_tool)
                else:
                    # Pass through if it doesn't match expected structure (might already be correct or different type)
                    transformed_tools.append(tool)
            
            params["tools"] = transformed_tools
            if tool_choice:
                params["tool_choice"] = tool_choice
        
        # Add any additional kwargs
        params.update({k: v for k, v in kwargs.items() if k not in params})
        
        # Use new Responses API
        response = await self.client.responses.create(**params)
        
        # Convert to framework format
        # The Responses API has a convenience property for text
        final_content = response.output_text if hasattr(response, "output_text") else ""
        
        tool_calls_obj = None
        
        # Iterate through output items to find tool calls
        if response.output:
            for item in response.output:
                # Based on SDK, tool calls have types like "function_call"
                if item.type == "function_call":
                    if tool_calls_obj is None:
                        tool_calls_obj = []
                    
                    # SDK: ResponseFunctionToolCallMessage has fields: name, arguments, call_id
                    tool_calls_obj.append(
                        ToolCallMessage(
                            id=getattr(item, "call_id", getattr(item, "id", None)),
                            type="function",
                            function={
                                "name": item.name,
                                "arguments": item.arguments
                            }
                        )
                    )
                # Handle other tool call types if necessary (mcp_call, etc.) in the future
        
        # Usage mapping
        usage_dict = None
        if hasattr(response, "usage") and response.usage:
            usage_dict = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ModelResponse(
            role="assistant",
            content=final_content,
            tool_calls=tool_calls_obj,
            usage=usage_dict,
            model=response.model if hasattr(response, "model") else self.model,
            finish_reason=None,
        )
    
    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
        **kwargs
    ) -> AsyncIterator[ModelResponse]:
        """Generate a streaming response from OpenAI using Responses API."""
        instructions = ""
        conversation_input = []
        for msg in messages:
            if msg.role == "system":
                instructions += f"{msg.content}\n"
            elif msg.role == "user":
                conversation_input.append({
                    "type": "message",
                    "role": "user",
                    "content": msg.content
                })
            elif msg.role == "assistant":
                if msg.content:
                    conversation_input.append({
                        "type": "message",
                        "role": "assistant",
                        "content": msg.content
                    })
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        conversation_input.append({
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.function["name"],
                            "arguments": tc.function["arguments"]
                        })
            elif msg.role == "tool":
                conversation_input.append({
                    "type": "function_call_output",
                    "call_id": getattr(msg, "tool_call_id", None),
                    "output": msg.content
                })

        params = {
            "model": self.model,
            "input": conversation_input,
            "temperature": kwargs.get("temperature", self.temperature),
            "stream": True,
        }
        if instructions:
            params["instructions"] = instructions.strip()
        if self.max_tokens:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        if tools:
            transformed_tools = []
            for tool in tools:
                if tool.get("type") == "function" and "function" in tool:
                    fn_def = tool["function"]
                    new_tool = {
                        "type": "function",
                        "name": fn_def.get("name"),
                        "description": fn_def.get("description"),
                        "parameters": fn_def.get("parameters"),
                    }
                    transformed_tools.append(new_tool)
                else:
                    transformed_tools.append(tool)
            params["tools"] = transformed_tools
            if tool_choice:
                params["tool_choice"] = tool_choice
        params.update({k: v for k, v in kwargs.items() if k not in params})

        # Streaming loop
        content_accum = ""
        tool_calls_obj = None
        usage_dict = None
        model_name = self.model
        stream = await self.client.responses.create(**params)
        async for event in stream:
            # Accumulate incremental content for internal assembly, but yield only the delta
            chunk = ""
            if hasattr(event, "output_text") and event.output_text:
                chunk += event.output_text
            if hasattr(event, "text") and event.text:
                chunk += event.text
            if hasattr(event, "delta") and event.delta:
                if isinstance(event.delta, dict):
                    chunk += event.delta.get("content", "")
                elif isinstance(event.delta, str):
                    chunk += event.delta

            # Detect function call outputs in the event
            event_tool_calls = None
            if hasattr(event, "output") and event.output:
                for item in event.output:
                    if item.type == "function_call":
                        if event_tool_calls is None:
                            event_tool_calls = []
                        event_tool_calls.append(
                            ToolCallMessage(
                                id=getattr(item, "call_id", getattr(item, "id", None)),
                                type="function",
                                function={
                                    "name": item.name,
                                    "arguments": item.arguments
                                }
                            )
                        )

            if hasattr(event, "usage") and event.usage:
                usage_dict = {
                    "prompt_tokens": event.usage.input_tokens,
                    "completion_tokens": event.usage.output_tokens,
                    "total_tokens": event.usage.total_tokens,
                }
            if hasattr(event, "model"):
                model_name = event.model

            # Update internal accumulator
            if chunk:
                content_accum += chunk

            # If we have a tool call emitted, yield a response with tool_calls and no content delta
            if event_tool_calls:
                yield ModelResponse(
                    role="assistant",
                    content="",
                    tool_calls=event_tool_calls,
                    usage=usage_dict,
                    model=model_name,
                    finish_reason=None,
                )
                # Tool calls usually terminate the generation; continue to next events
                continue

            # For normal incremental text, yield only the delta with metadata marking it partial
            if chunk:
                yield ModelResponse(
                    role="assistant",
                    content=chunk,
                    tool_calls=None,
                    usage=usage_dict,
                    model=model_name,
                    finish_reason=None,
                    metadata={"partial": True},
                )

        # When the stream completes, yield a final message indicating completion
        if content_accum:
            yield ModelResponse(
                role="assistant",
                content="",
                tool_calls=None,
                usage=usage_dict,
                model=model_name,
                finish_reason="stop",
                metadata={"partial": False, "complete": True},
            )
    
    def count_tokens(self, messages: list[BaseClientMessage]) -> int:
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
