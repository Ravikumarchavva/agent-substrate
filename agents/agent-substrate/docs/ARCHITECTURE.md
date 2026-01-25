# Agent Framework Architecture

## Overview

This is a production-ready agentic framework designed for building autonomous AI agents with tool calling, memory management, and observability. The framework follows industry best practices and is inspired by patterns from LangChain, AutoGen, and OpenAI's Assistant API.

## Design Principles

### 1. **Protocol-Oriented Design**
- Abstract base classes define contracts for all major components
- Implementations can be swapped without changing core logic
- Easy to extend with new model providers, memory systems, or tools

### 2. **Type Safety First**
- Pydantic models for all data structures
- Full type hints throughout the codebase
- Runtime validation of inputs/outputs

### 3. **Async by Default**
- All I/O operations are async (API calls, tool execution)
- Efficient handling of concurrent operations
- Streaming support for real-time responses

### 4. **Production Ready**
- Comprehensive error handling
- Built-in observability (logging, tracing, metrics)
- State management and persistence
- Token counting and cost tracking

### 5. **Separation of Concerns**
- Clear boundaries between components
- Single responsibility principle
- Testable, maintainable code

## Core Components

### 1. Messages (`src/agent_framework/messages/`)

The message system provides structured communication between agents, users, and tools.

```
BaseMessage (abstract)
├── SystemMessage      # System instructions
├── UserMessage        # User inputs (text/multimodal)
├── AssistantMessage   # Agent responses (with optional tool calls)
└── ToolMessage        # Tool execution results
```

**Key Features:**
- UUID tracking for message lineage
- Timestamps for all messages
- Metadata support for custom fields
- Serialization for storage/transmission
- Support for OpenAI function calling format

**Design Decisions:**
- Messages are immutable (Pydantic BaseModel)
- Each message has a unique ID for tracing
- Tool calls are embedded in AssistantMessage (OpenAI pattern)
- Support for multimodal content (future: images, audio)

---

### 2. Model Clients (`src/agent_framework/model_clients/`)

Abstraction layer for different LLM providers.

```
BaseModelClient (abstract)
├── OpenAIClient       # OpenAI/Azure OpenAI
├── AnthropicClient    # Claude (future)
├── GeminiClient       # Google Gemini (future)
└── OllamaClient       # Local models (future)
```

**Key Features:**
- Unified interface across providers
- Streaming and non-streaming support
- Token counting for cost tracking
- Tool calling schema translation
- Response normalization

**Design Decisions:**
- Async-only interface (no sync methods)
- Automatic retry with exponential backoff (future)
- Response caching support (future)
- Token counting uses provider-specific methods

---

### 3. Tools (`src/agent_framework/tools/`)

Function calling system for agent actions.

```
BaseTool (abstract)
├── CalculatorTool
├── WebSearchTool
├── FileOperationsTool
├── CodeExecutionTool
└── [Custom Tools]
```

**Key Features:**
- JSON Schema validation for parameters
- Async execution
- Error handling with graceful fallbacks
- OpenAI function calling format
- Automatic schema generation

**Design Decisions:**
- Tools return JSON strings (parseable by LLM)
- Each tool defines its own schema
- Tools are stateless (no side effects in constructor)
- Support for required/optional parameters

---

### 4. Memory (`src/agent_framework/memory/`)

Conversation history management with context window optimization.

```
BaseMemory (abstract)
├── UnboundedMemory      # Stores all messages (development)
├── SlidingWindowMemory  # Keep last N messages (future)
├── TokenLimitMemory     # Stay within token budget (future)
└── VectorMemory         # Semantic retrieval (future)
```

**Key Features:**
- Token-aware storage
- Multiple retrieval strategies
- Persistence support
- Message filtering and search

**Design Decisions:**
- Memory is separate from agent (composable)
- Token counting for context management
- Support for different retention policies
- Easy serialization for persistence

---

### 5. Agents (`src/agent_framework/agents/`)

The orchestration layer that ties everything together.

```
BaseAgent (abstract)
├── ReActAgent          # Reasoning + Acting loop
├── ConversationalAgent # Simple chat agent
├── PlannerAgent        # Multi-step planning (future)
└── MultiAgent          # Agent collaboration (future)
```

**Key Features:**
- Autonomous tool calling loop
- Memory integration
- Streaming responses
- State management (save/load)
- Observability hooks

**Design Decisions:**
- Agents own their execution loop
- Configurable max iterations (prevent infinite loops)
- Support for tool choice strategies (auto, required, none)
- Built-in retry logic for failed tool calls

---

### 6. Observability (`src/agent_framework/observability/`)

Monitoring, logging, and debugging infrastructure.

**Features:**
- Structured logging (JSON format)
- Distributed tracing (spans for each operation)
- Token usage tracking
- Cost estimation
- Performance metrics
- Event hooks for custom monitoring

**Integration Points:**
- OpenTelemetry support (future)
- LangSmith/Weights & Biases integration (future)
- Custom callback handlers

---

### 7. Configuration (`src/agent_framework/configs/`)

Centralized configuration management.

**Features:**
- Environment variable support
- YAML/JSON config files
- Type-safe configuration classes
- Validation on load
- Secret management

---

## Agent Execution Flow

### ReAct Agent (Reasoning + Acting)

```
1. User Input
   ↓
2. Add to Memory
   ↓
3. Generate Response (with tools available)
   ↓
4. Check Response:
   - Has tool calls? → Execute tools → Add results to memory → Go to step 3
   - No tool calls? → Return final response
   ↓
5. Add final response to memory
   ↓
6. Return to user
```

**Max Iterations:** Configurable limit to prevent infinite loops (default: 10)

**Error Handling:**
- Tool execution errors are fed back to LLM
- LLM can retry with corrected parameters
- Maximum retry count per tool call

---

## Data Flow

```
User Input (string)
    ↓
UserMessage (structured)
    ↓
Memory.add_message()
    ↓
Agent.run()
    ├─→ Memory.get_messages()
    ├─→ ModelClient.generate(messages, tools)
    ├─→ Tool.execute() [if tool calls]
    └─→ Memory.add_message() [results]
    ↓
AssistantMessage (structured)
    ↓
Response (string)
```

---

## Extension Points

### 1. Custom Model Client
```python
class CustomClient(BaseModelClient):
    async def generate(self, messages, tools, **kwargs):
        # Your implementation
        pass
```

### 2. Custom Tool
```python
class MyTool(BaseTool):
    def get_schema(self):
        return {...}
    
    async def execute(self, **kwargs):
        # Your implementation
        return json.dumps(result)
```

### 3. Custom Memory
```python
class MyMemory(BaseMemory):
    def add_message(self, message):
        # Your implementation
        pass
```

### 4. Custom Agent
```python
class MyAgent(BaseAgent):
    async def run(self, user_input: str):
        # Your custom execution logic
        pass
```

---

## Best Practices

### 1. **Error Handling**
- Always wrap tool execution in try/except
- Return error information to LLM for correction
- Log all errors with context

### 2. **Token Management**
- Monitor token usage per request
- Implement memory pruning strategies
- Use streaming for long responses

### 3. **Tool Design**
- Keep tools focused (single responsibility)
- Return structured JSON
- Include error states in schema
- Document expected inputs clearly

### 4. **Security**
- Validate all tool inputs
- Sanitize user inputs before tool execution
- Use environment variables for secrets
- Rate limit expensive operations

### 5. **Testing**
- Mock model clients for unit tests
- Use test tools for integration tests
- Verify message serialization
- Test error paths

---

## Production Considerations

### 1. **Scaling**
- Stateless agents (state in memory/DB)
- Connection pooling for model clients
- Async everywhere
- Caching of expensive operations

### 2. **Monitoring**
- Track token usage and costs
- Monitor tool execution times
- Alert on errors and failures
- Dashboard for agent performance

### 3. **Deployment**
- Containerize with Docker
- Use environment-based configuration
- Health check endpoints
- Graceful shutdown handling

### 4. **Cost Optimization**
- Use appropriate model sizes
- Implement response caching
- Batch operations where possible
- Set token limits

---

## Future Enhancements

### Phase 1 (Current)
- [x] Core message types
- [x] OpenAI client
- [x] Basic tool system
- [x] Simple memory
- [ ] ReAct agent implementation
- [ ] Observability basics

### Phase 2
- [ ] Streaming support
- [ ] More memory strategies
- [ ] Anthropic client
- [ ] State persistence
- [ ] Advanced error recovery

### Phase 3
- [ ] Multi-agent systems
- [ ] Vector memory
- [ ] Advanced planning
- [ ] Tool composition
- [ ] Human-in-the-loop

### Phase 4
- [ ] Web UI
- [ ] API server
- [ ] Deployment templates
- [ ] Production monitoring
- [ ] Cost analytics

---

## References

**Influenced By:**
- OpenAI Assistant API
- LangChain Agent Framework
- AutoGen Multi-Agent System
- Semantic Kernel (Microsoft)
- Haystack Agent System

**Standards:**
- OpenAI Function Calling Spec
- Anthropic Tool Use Format
- JSON Schema for validation
- OpenTelemetry for tracing
