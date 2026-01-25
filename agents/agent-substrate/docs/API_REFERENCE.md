# API Reference

## Core API Design

This document provides the complete API reference for the Agent Framework.

---

## Messages API

### BaseMessage

Abstract base class for all message types.

```python
from agent_framework.messages.base_message import BaseMessage

class BaseMessage(BaseModel):
    id: str                          # Unique identifier (UUID)
    role: Literal["system", "user", "assistant", "tool"]
    content: Any                     # Message content
    timestamp: datetime              # Creation time (UTC)
    metadata: dict[str, Any]         # Custom metadata
    
    def to_dict(self) -> dict:
        """Convert to LLM API format."""
    
    def to_storage_dict(self) -> dict:
        """Convert to storage format (includes all fields)."""
    
    @classmethod
    def from_dict(cls, data: dict) -> BaseMessage:
        """Create from dictionary."""
```

### SystemMessage

System instructions for the agent.

```python
from agent_framework.messages.agent_messages import SystemMessage

msg = SystemMessage(
    content="You are a helpful assistant specialized in Python programming."
)
```

### UserMessage

User input message (text or multimodal).

```python
from agent_framework.messages.agent_messages import UserMessage

# Text message
msg = UserMessage(
    content="What's the weather today?",
    name="john_doe"  # Optional user identifier
)

# Multimodal message (future)
msg = UserMessage(
    content=[
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "https://..."}}
    ]
)
```

### AssistantMessage

Agent response with optional tool calls.

```python
from agent_framework.messages.agent_messages import AssistantMessage, ToolCall

# Simple response
msg = AssistantMessage(
    content="The weather is sunny today."
)

# Response with tool call
msg = AssistantMessage(
    content="Let me check the weather for you.",
    tool_calls=[
        ToolCall(
            id="call_123",
            type="function",
            function={
                "name": "get_weather",
                "arguments": '{"location": "San Francisco"}'
            }
        )
    ]
)
```

### ToolMessage

Tool execution result.

```python
from agent_framework.messages.agent_messages import ToolMessage

msg = ToolMessage(
    content='{"temperature": 72, "condition": "sunny"}',
    tool_call_id="call_123",
    name="get_weather"
)
```

---

## Model Clients API

### BaseModelClient

Abstract interface for all model providers.

```python
from agent_framework.model_clients.base_client import BaseModelClient

class BaseModelClient:
    def __init__(
        self,
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ):
        """Initialize model client."""
    
    async def generate(
        self,
        messages: list[BaseMessage],
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> ModelResponse:
        """Generate a single response."""
    
    async def generate_stream(
        self,
        messages: list[BaseMessage],
        tools: Optional[list[dict]] = None,
        **kwargs
    ) -> AsyncIterator[ModelResponse]:
        """Generate streaming response."""
    
    def count_tokens(self, messages: list[BaseMessage]) -> int:
        """Count tokens in messages."""
```

### OpenAIClient

OpenAI/Azure OpenAI implementation.

```python
from agent_framework.model_clients.openai_client import OpenAIClient

# Initialize
client = OpenAIClient(
    model="gpt-4o",
    api_key="sk-...",  # Or set OPENAI_API_KEY env var
    temperature=0.7,
    max_tokens=1000
)

# Generate response
response = await client.generate(
    messages=[UserMessage(content="Hello!")],
    tools=None
)

# Generate with tools
response = await client.generate(
    messages=[UserMessage(content="What's 2+2?")],
    tools=[calculator_tool.get_schema()],
    tool_choice="auto"  # or "required" or {"type": "function", "function": {"name": "..."}}
)

# Streaming
async for chunk in client.generate_stream(messages=messages):
    print(chunk.content, end="", flush=True)

# Count tokens
token_count = client.count_tokens(messages)
```

**Parameters:**
- `model`: Model name (e.g., "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo")
- `api_key`: OpenAI API key (optional if set via env var)
- `temperature`: Sampling temperature (0-2, default: 0.7)
- `max_tokens`: Maximum tokens in response (optional)
- `tool_choice`: "auto", "required", "none", or specific function

---

## Tools API

### BaseTool

Abstract base class for all tools.

```python
from agent_framework.tools.base_tool import BaseTool

class BaseTool(ABC):
    def __init__(self, name: str, description: str):
        """Initialize tool with name and description."""
    
    async def execute(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""
    
    def get_schema(self) -> dict[str, Any]:
        """Return OpenAI function calling schema."""
```

### Creating Custom Tools

```python
from agent_framework.tools.base_tool import BaseTool
import json

class WeatherTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="get_weather",
            description="Get current weather for a location"
        )
    
    async def execute(self, location: str, units: str = "celsius") -> str:
        """Execute weather lookup.
        
        Args:
            location: City name or coordinates
            units: Temperature units (celsius/fahrenheit)
        
        Returns:
            JSON string with weather data
        """
        # Your implementation
        result = {
            "location": location,
            "temperature": 22,
            "condition": "sunny",
            "units": units
        }
        return json.dumps(result)
    
    def get_schema(self) -> dict[str, Any]:
        """Return OpenAI function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name or coordinates (e.g., 'London', '40.7,-74.0')"
                        },
                        "units": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                            "description": "Temperature units",
                            "default": "celsius"
                        }
                    },
                    "required": ["location"]
                }
            }
        }
```

### Built-in Tools

```python
from agent_framework.tools.example_tools import (
    CalculatorTool,
    GetCurrentTimeTool,
    WebSearchTool
)

# Calculator
calc = CalculatorTool()
result = await calc.execute(expression="2 + 2")  # '{"result": 4, "expression": "2 + 2"}'

# Current time
timer = GetCurrentTimeTool()
result = await timer.execute(timezone="UTC")

# Web search (placeholder)
search = WebSearchTool()
result = await search.execute(query="Python tutorials", num_results=5)
```

---

## Memory API

### BaseMemory

Abstract interface for memory systems.

```python
from agent_framework.memory.base_memory import BaseMemory

class BaseMemory(ABC):
    def add_message(self, message: BaseMessage) -> None:
        """Add a message to memory."""
    
    def get_messages(self, limit: Optional[int] = None) -> list[BaseMessage]:
        """Retrieve messages from memory."""
    
    def clear(self) -> None:
        """Clear all messages."""
    
    def get_token_count(self) -> int:
        """Get approximate token count."""
    
    def __len__(self) -> int:
        """Return number of messages."""
```

### UnboundedMemory

Stores all messages without limit.

```python
from agent_framework.memory.unbounded_memory import UnboundedMemory

memory = UnboundedMemory()

# Add messages
memory.add_message(UserMessage(content="Hello"))
memory.add_message(AssistantMessage(content="Hi there!"))

# Retrieve all messages
messages = memory.get_messages()

# Retrieve last N messages
recent = memory.get_messages(limit=5)

# Check size
print(len(memory))  # Number of messages
print(memory.get_token_count())  # Approximate tokens

# Clear memory
memory.clear()
```

**Future Memory Types:**

```python
# Sliding window (keep last N messages)
from agent_framework.memory.sliding_window_memory import SlidingWindowMemory
memory = SlidingWindowMemory(window_size=10)

# Token-limited (stay within context window)
from agent_framework.memory.token_limit_memory import TokenLimitMemory
memory = TokenLimitMemory(max_tokens=4000, model_client=client)

# Vector memory (semantic retrieval)
from agent_framework.memory.vector_memory import VectorMemory
memory = VectorMemory(embedding_client=embeddings, top_k=5)
```

---

## Agent API

### BaseAgent

Abstract base class for all agents.

```python
from agent_framework.agents.base_agent import BaseAgent

class BaseAgent(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        tools: Optional[list[BaseTool]] = None,
        system_instructions: str = "you are a helpful assistant",
        memory: Optional[BaseMemory] = None,
    ):
        """Initialize agent."""
    
    async def run(self, *args, **kwargs):
        """Run agent (implementation-specific)."""
    
    async def run_stream(self, *args, **kwargs):
        """Run agent with streaming."""
    
    def save_state(self):
        """Save agent state to disk."""
    
    def load_state(self):
        """Load agent state from disk."""
```

### ReActAgent (Future Implementation)

Reasoning + Acting agent with tool loop.

```python
from agent_framework.agents.react_agent import ReActAgent
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.tools.example_tools import CalculatorTool

# Create agent
agent = ReActAgent(
    name="math_assistant",
    description="An agent that helps with math",
    model_client=OpenAIClient(model="gpt-4o"),
    tools=[CalculatorTool()],
    system_instructions="You are a math tutor. Use the calculator for computations.",
    memory=UnboundedMemory(),
    max_iterations=10,  # Prevent infinite loops
    verbose=True  # Log each step
)

# Run agent
response = await agent.run("What's 123 * 456?")
print(response)

# Streaming response
async for chunk in agent.run_stream("Calculate 99 factorial"):
    print(chunk, end="", flush=True)

# Save/load state
agent.save_state("agent_state.json")
agent.load_state("agent_state.json")
```

**Agent Configuration:**

```python
agent = ReActAgent(
    name="assistant",
    description="General purpose assistant",
    model_client=client,
    tools=tools,
    system_instructions="Custom instructions...",
    memory=memory,
    
    # Execution control
    max_iterations=10,           # Max tool loops
    max_tool_retries=2,          # Retries per tool call
    tool_choice="auto",          # auto/required/none
    
    # Observability
    verbose=True,                # Log each step
    callbacks=[my_callback],     # Custom hooks
    
    # Performance
    parallel_tool_calls=True,    # Execute tools in parallel
    timeout=60.0,                # Overall timeout (seconds)
)
```

---

## Configuration API

### Environment Variables

```bash
# Model provider
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Observability
LOG_LEVEL=INFO
ENABLE_TRACING=true

# Performance
MAX_CONCURRENT_TOOLS=5
REQUEST_TIMEOUT=30
```

### Configuration Class (Future)

```python
from agent_framework.configs import AgentConfig

config = AgentConfig.from_yaml("config.yaml")
# or
config = AgentConfig.from_env()

agent = ReActAgent.from_config(config)
```

---

## Observability API (Future)

### Logging

```python
from agent_framework.observability import setup_logging

setup_logging(
    level="INFO",
    format="json",  # or "text"
    output="file.log"  # or stdout
)
```

### Callbacks

```python
from agent_framework.observability import AgentCallback

class MyCallback(AgentCallback):
    async def on_agent_start(self, agent, input):
        print(f"Agent {agent.name} starting...")
    
    async def on_tool_call(self, tool, args):
        print(f"Calling {tool.name} with {args}")
    
    async def on_agent_end(self, agent, output):
        print(f"Agent finished: {output}")

agent = ReActAgent(..., callbacks=[MyCallback()])
```

### Tracing

```python
from agent_framework.observability import enable_tracing

enable_tracing(
    service_name="my-agent",
    endpoint="http://jaeger:14268/api/traces"
)
```

---

## Error Handling

### Common Exceptions

```python
from agent_framework.exceptions import (
    AgentError,              # Base exception
    ModelClientError,        # Model API errors
    ToolExecutionError,      # Tool failures
    MaxIterationsExceeded,   # Too many loops
    TokenLimitExceeded,      # Context window full
)

try:
    response = await agent.run("Hello")
except MaxIterationsExceeded:
    print("Agent couldn't complete in time")
except ToolExecutionError as e:
    print(f"Tool failed: {e.tool_name}")
except AgentError as e:
    print(f"General error: {e}")
```

---

## Type Hints

The framework uses comprehensive type hints:

```python
from typing import Optional, AsyncIterator
from agent_framework.messages import BaseMessage, UserMessage
from agent_framework.tools import BaseTool

async def process_messages(
    messages: list[BaseMessage],
    tools: Optional[list[BaseTool]] = None
) -> AsyncIterator[str]:
    """Fully typed function."""
    ...
```

---

## Testing Utilities (Future)

```python
from agent_framework.testing import (
    MockModelClient,
    MockTool,
    create_test_messages
)

# Mock client for testing
client = MockModelClient(
    responses=["Hello!", "I'll help with that"]
)

# Test agent
agent = ReActAgent(model_client=client, ...)
response = await agent.run("test")
assert "Hello" in response
```

---

## Best Practices

1. **Always use async/await** for I/O operations
2. **Handle errors explicitly** with try/except
3. **Set max_iterations** to prevent infinite loops
4. **Monitor token usage** to manage costs
5. **Use type hints** for better IDE support
6. **Validate tool inputs** before execution
7. **Log important events** for debugging
8. **Test with mocks** before production
