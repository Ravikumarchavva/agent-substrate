"""OpenAI model client implementation."""
from typing import Any, AsyncIterator, Optional
import json
from openai import AsyncOpenAI
import tiktoken

from .base_client import BaseModelClient, ModelResponse
from agent_framework.messages.base_message import BaseMessage
from agent_framework.messages.agent_messages import (
    AssistantMessage,
    ToolCall,
    SystemMessage,
    UserMessage,
    ToolMessage
)


class OpenAIClient(BaseModelClient):
    """OpenAI API client with support for chat completions and tool calling."""
    
    def __init__(
        self,
        model: str = "gpt-4o",
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
    
    def _messages_to_openai_format(self, messages: list[BaseMessage]) -> list[dict]:
        """Convert framework messages to OpenAI API format."""
        return [msg.to_dict() for msg in messages]
    
    def _tools_to_openai_format(self, tools: Optional[list[dict]]) -> Optional[list[dict]]:
        """Convert tools to OpenAI function calling format."""
        if not tools:
            return None
        return tools
    
    async def generate(
        self,
        messages: list[BaseMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
        **kwargs
    ) -> ModelResponse:
        """Generate a single response from OpenAI."""
        openai_messages = self._messages_to_openai_format(messages)
        
        params = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        if self.max_tokens:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        
        if tools:
            params["tools"] = self._tools_to_openai_format(tools)
            if tool_choice:
                params["tool_choice"] = tool_choice
        
        # Add any additional kwargs
        params.update({k: v for k, v in kwargs.items() if k not in params})
        
        response = await self.client.chat.completions.create(**params)
        
        # Convert to framework format
        choice = response.choices[0]
        message = choice.message
        
        tool_calls_obj = None
        if message.tool_calls:
            tool_calls_obj = [
                ToolCall(
                    id=tc.id,
                    type=tc.type,
                    function={
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                )
                for tc in message.tool_calls
            ]
        
        return ModelResponse(
            role="assistant",
            content=message.content,
            tool_calls=tool_calls_obj,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            model=response.model,
            finish_reason=choice.finish_reason,
        )
    
    async def generate_stream(
        self,
        messages: list[BaseMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
        **kwargs
    ) -> AsyncIterator[ModelResponse]:
        """Generate a streaming response from OpenAI."""
        openai_messages = self._messages_to_openai_format(messages)
        
        params = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "stream": True,
        }
        
        if self.max_tokens:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        
        if tools:
            params["tools"] = self._tools_to_openai_format(tools)
            if tool_choice:
                params["tool_choice"] = tool_choice
        
        params.update({k: v for k, v in kwargs.items() if k not in params and k != "stream"})
        
        stream = await self.client.chat.completions.create(**params)
        
        async for chunk in stream:
            if not chunk.choices:
                continue
            
            delta = chunk.choices[0].delta
            
            # Handle tool calls in streaming
            tool_calls_obj = None
            if delta.tool_calls:
                tool_calls_obj = [
                    ToolCall(
                        id=tc.id or "",
                        type=tc.type or "function",
                        function={
                            "name": tc.function.name if tc.function.name else "",
                            "arguments": tc.function.arguments if tc.function.arguments else ""
                        }
                    )
                    for tc in delta.tool_calls
                ]
            
            yield ModelResponse(
                role="assistant",
                content=delta.content or "",
                tool_calls=tool_calls_obj,
                model=chunk.model,
                finish_reason=chunk.choices[0].finish_reason,
            )
    
    def count_tokens(self, messages: list[BaseMessage]) -> int:
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
