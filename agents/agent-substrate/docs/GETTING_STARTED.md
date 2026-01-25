# Getting Started with Agent Framework

Welcome! This guide will help you get started with the Agent Framework in 10 minutes.

---

## Installation

### Prerequisites

- Python 3.13+
- pip or uv package manager
- OpenAI API key (or other LLM provider)

### Install

```bash
# Clone the repository
git clone https://github.com/yourusername/agent-framework.git
cd agent-framework

# Install dependencies (using uv)
uv sync

# Or using pip
pip install -e .
```

### Set up API Keys

```bash
# Add to your .env file or export
export OPENAI_API_KEY="sk-your-key-here"
```

---

## Quick Start: Your First Agent

### 1. Simple Conversational Agent

Create a file `examples/simple_agent.py`:

```python
import asyncio
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.agent_messages import UserMessage, SystemMessage

async def main():
    # Initialize components
    client = OpenAIClient(model="gpt-4o", temperature=0.7)
    memory = UnboundedMemory()
    
    # Add system instructions
    system_msg = SystemMessage(
        content="You are a helpful assistant that specializes in Python programming."
    )
    memory.add_message(system_msg)
    
    # User message
    user_msg = UserMessage(content="How do I read a CSV file in Python?")
    memory.add_message(user_msg)
    
    # Get response
    messages = memory.get_messages()
    response = await client.generate(messages=messages)
    
    # Print response
    print(f"Assistant: {response.content}")
    
    # Add to memory for conversation continuity
    memory.add_message(response)

if __name__ == "__main__":
    asyncio.run(main())
```

Run it:
```bash
python examples/simple_agent.py
```

---

### 2. Agent with Tools

Create `examples/agent_with_tools.py`:

```python
import asyncio
import json
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.agent_messages import (
    UserMessage, SystemMessage, ToolMessage
)
from agent_framework.tools.example_tools import CalculatorTool, GetCurrentTimeTool

async def run_agent_with_tools():
    # Initialize
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()
    tools = [CalculatorTool(), GetCurrentTimeTool()]
    
    # System message
    system_msg = SystemMessage(
        content="You are a helpful assistant with access to tools. Use them when needed."
    )
    memory.add_message(system_msg)
    
    # User asks a math question
    user_msg = UserMessage(content="What's 1234 multiplied by 5678?")
    memory.add_message(user_msg)
    
    # Tool calling loop
    max_iterations = 5
    for iteration in range(max_iterations):
        print(f"\n--- Iteration {iteration + 1} ---")
        
        # Get response with tools
        messages = memory.get_messages()
        tool_schemas = [tool.get_schema() for tool in tools]
        
        response = await client.generate(
            messages=messages,
            tools=tool_schemas,
            tool_choice="auto"
        )
        
        # No tool calls? We're done!
        if not response.tool_calls:
            print(f"Assistant: {response.content}")
            memory.add_message(response)
            break
        
        # Add assistant message with tool calls
        memory.add_message(response)
        print(f"Assistant: {response.content or '(calling tools)'}")
        
        # Execute each tool call
        for tool_call in response.tool_calls:
            tool_name = tool_call.function["name"]
            tool_args = json.loads(tool_call.function["arguments"])
            
            print(f"Calling tool: {tool_name} with {tool_args}")
            
            # Find and execute tool
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool:
                result = await tool.execute(**tool_args)
                print(f"Tool result: {result}")
                
                # Add tool result to memory
                tool_msg = ToolMessage(
                    content=result,
                    tool_call_id=tool_call.id,
                    name=tool_name
                )
                memory.add_message(tool_msg)
    else:
        print("\nMax iterations reached!")

if __name__ == "__main__":
    asyncio.run(run_agent_with_tools())
```

Run it:
```bash
python examples/agent_with_tools.py
```

Expected output:
```
--- Iteration 1 ---
Assistant: (calling tools)
Calling tool: calculator with {'expression': '1234 * 5678'}
Tool result: {"result": 7006652, "expression": "1234 * 5678"}

--- Iteration 2 ---
Assistant: The result of 1234 multiplied by 5678 is 7,006,652.
```

---

### 3. Streaming Responses

Create `examples/streaming_agent.py`:

```python
import asyncio
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.messages.agent_messages import UserMessage, SystemMessage
from agent_framework.memory.unbounded_memory import UnboundedMemory

async def streaming_example():
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()
    
    memory.add_message(SystemMessage(content="You are a creative writer."))
    memory.add_message(UserMessage(content="Write a short poem about coding."))
    
    print("Assistant: ", end="", flush=True)
    
    full_response = ""
    async for chunk in client.generate_stream(messages=memory.get_messages()):
        if chunk.content:
            print(chunk.content, end="", flush=True)
            full_response += chunk.content
    
    print("\n")

if __name__ == "__main__":
    asyncio.run(streaming_example())
```

---

## Creating Custom Tools

### Simple Tool Example

```python
from agent_framework.tools.base_tool import BaseTool
import json
import requests

class WeatherTool(BaseTool):
    """Get weather information."""
    
    def __init__(self, api_key: str):
        super().__init__(
            name="get_weather",
            description="Get current weather for a location"
        )
        self.api_key = api_key
    
    async def execute(self, location: str, units: str = "celsius") -> str:
        """Get weather for location."""
        try:
            # Your API call here
            # response = requests.get(f"https://api.weather.com/...")
            
            # Mock response for example
            result = {
                "location": location,
                "temperature": 22,
                "condition": "sunny",
                "units": units,
                "humidity": 65
            }
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def get_schema(self) -> dict:
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
                            "description": "City name (e.g., 'London', 'New York')"
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

# Usage
weather = WeatherTool(api_key="your-key")
result = await weather.execute(location="London", units="celsius")
```

---

## Memory Management

### Different Memory Types

```python
# 1. Unbounded (stores everything)
from agent_framework.memory.unbounded_memory import UnboundedMemory
memory = UnboundedMemory()

# 2. Sliding Window (future - keeps last N messages)
# from agent_framework.memory.sliding_window_memory import SlidingWindowMemory
# memory = SlidingWindowMemory(window_size=20)

# 3. Token Limited (future - stays within token budget)
# from agent_framework.memory.token_limit_memory import TokenLimitMemory
# memory = TokenLimitMemory(max_tokens=4000, model_client=client)
```

### Working with Memory

```python
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.agent_messages import UserMessage

memory = UnboundedMemory()

# Add messages
memory.add_message(UserMessage(content="Hello"))

# Get all messages
all_messages = memory.get_messages()

# Get last N messages
recent = memory.get_messages(limit=5)

# Check size
print(f"Messages: {len(memory)}")
print(f"Approx tokens: {memory.get_token_count()}")

# Clear memory
memory.clear()
```

---

## Configuration

### Using Environment Variables

Create `.env` file:
```bash
OPENAI_API_KEY=sk-...
AGENT_LOG_LEVEL=INFO
AGENT_MAX_ITERATIONS=10
```

Load in your code:
```python
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAIClient(api_key=api_key)
```

---

## Best Practices

### 1. Always Use Async/Await

```python
# ✅ Good
async def main():
    response = await client.generate(messages)

# ❌ Bad
def main():
    response = client.generate(messages)  # Won't work!
```

### 2. Handle Errors

```python
try:
    response = await client.generate(messages)
except Exception as e:
    print(f"Error: {e}")
    # Handle gracefully
```

### 3. Set Max Iterations

```python
max_iterations = 10  # Prevent infinite loops
for i in range(max_iterations):
    # Your agent loop
    ...
```

### 4. Monitor Token Usage

```python
token_count = client.count_tokens(messages)
print(f"Tokens: {token_count}")

if token_count > 3000:
    # Prune memory or summarize
    pass
```

### 5. Use System Messages

```python
system_msg = SystemMessage(
    content="""You are a Python expert.
    - Provide clear, working code examples
    - Explain your reasoning
    - Ask for clarification when needed"""
)
```

---

## Common Patterns

### Pattern 1: Question-Answer Agent

```python
async def qa_agent(question: str):
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()
    
    memory.add_message(SystemMessage(content="You are a helpful Q&A assistant."))
    memory.add_message(UserMessage(content=question))
    
    response = await client.generate(messages=memory.get_messages())
    return response.content
```

### Pattern 2: Multi-Turn Conversation

```python
async def chat_session():
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()
    memory.add_message(SystemMessage(content="You are a helpful assistant."))
    
    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            break
        
        memory.add_message(UserMessage(content=user_input))
        response = await client.generate(messages=memory.get_messages())
        
        print(f"Assistant: {response.content}")
        memory.add_message(response)
```

### Pattern 3: Tool-Using Agent Loop

```python
async def tool_agent(user_input: str, tools: list, max_iterations: int = 5):
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()
    
    memory.add_message(SystemMessage(content="You are a helpful assistant."))
    memory.add_message(UserMessage(content=user_input))
    
    for iteration in range(max_iterations):
        response = await client.generate(
            messages=memory.get_messages(),
            tools=[t.get_schema() for t in tools]
        )
        
        if not response.tool_calls:
            return response.content
        
        memory.add_message(response)
        
        # Execute tools
        for tool_call in response.tool_calls:
            tool = next(t for t in tools if t.name == tool_call.function["name"])
            result = await tool.execute(**json.loads(tool_call.function["arguments"]))
            memory.add_message(ToolMessage(
                content=result,
                tool_call_id=tool_call.id,
                name=tool.name
            ))
    
    return "Max iterations reached"
```

---

## Next Steps

1. **Read the Architecture**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
2. **Explore API Reference**: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
3. **Check Component Specs**: [docs/COMPONENT_SPECS.md](docs/COMPONENT_SPECS.md)
4. **Build Custom Tools**: Extend `BaseTool` for your use case
5. **Deploy to Production**: See deployment guide (coming soon)

---

## Examples Directory

Check the `examples/` directory for more:

- `simple_chat.py` - Basic conversational agent
- `tool_calling.py` - Agent with multiple tools
- `streaming.py` - Streaming responses
- `memory_management.py` - Different memory strategies
- `custom_tools.py` - Creating custom tools
- `error_handling.py` - Robust error handling
- `production_agent.py` - Production-ready configuration

---

## Troubleshooting

### "OpenAI API key not found"
```bash
export OPENAI_API_KEY="sk-your-key"
```

### "Module not found"
```bash
# Install in editable mode
pip install -e .
```

### "Too many tokens"
```python
# Use a memory strategy with limits
memory = TokenLimitMemory(max_tokens=4000)
```

### "Agent stuck in loop"
```python
# Always set max_iterations
max_iterations = 10
```

---

## Getting Help

- **Documentation**: Check `/docs` directory
- **Examples**: See `/examples` directory
- **Issues**: GitHub Issues (future)
- **Discussions**: GitHub Discussions (future)

---

Happy building! 🚀
