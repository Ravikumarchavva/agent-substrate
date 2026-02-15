# Component Specifications

This document provides detailed specifications for each core component of the Agent Framework.

---

## 1. Message System

### Requirements

**Functional:**
- Support system, user, assistant, and tool message types
- Handle text and multimodal content
- Support OpenAI function calling format
- Enable message serialization/deserialization
- Track message metadata and timestamps

**Non-Functional:**
- Messages must be immutable
- Serialization latency < 1ms per message
- Support for 100K+ messages in memory
- Type-safe with Pydantic validation

### Message Flow

```
Input → UserMessage → Memory → Agent → ModelClient → AssistantMessage
                                            ↓
                                  [Tool Calls?]
                                       ↓ Yes
                                  ToolExecution → ToolMessage → Memory
                                       ↓
                                  Loop back to ModelClient
```

### Schema Definitions

**BaseMessage:**
```python
{
    "id": "uuid-v4",
    "role": "system|user|assistant|tool",
    "content": "string | list[dict] | null",
    "timestamp": "ISO 8601 datetime",
    "metadata": {"key": "value"}
}
```

**ToolCall:**
```python
{
    "id": "call_abc123",
    "type": "function",
    "function": {
        "name": "tool_name",
        "arguments": "{\"param\": \"value\"}"  # JSON string
    }
}
```

### Storage Format

Messages should be stored with all fields:
```json
{
    "id": "msg_123",
    "role": "assistant",
    "content": "Let me calculate that",
    "tool_calls": [...],
    "timestamp": "2026-01-25T10:30:00Z",
    "metadata": {
        "tokens": 15,
        "model": "gpt-4o",
        "latency_ms": 234
    }
}
```

---

## 2. Model Client System

### Requirements

**Functional:**
- Support multiple LLM providers (OpenAI, Anthropic, etc.)
- Handle streaming and non-streaming responses
- Convert between provider formats
- Count tokens accurately
- Support tool calling

**Non-Functional:**
- Async-only operations
- Retry with exponential backoff (3 retries)
- Request timeout: 60 seconds default
- Connection pooling for efficiency
- Rate limiting support

### Provider Normalization

Each provider has different formats. The client normalizes to our standard:

**OpenAI Format:**
```python
{
    "role": "assistant",
    "content": "text",
    "tool_calls": [...]
}
```

**Anthropic Format (different):**
```python
{
    "role": "assistant",
    "content": [
        {"type": "text", "text": "..."},
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
    ]
}
```

**Our Normalized Format:**
```python
ModelResponse(
    role="assistant",
    content="text",
    tool_calls=[ToolCall(...)],
    usage={...},
    model="gpt-4o",
    finish_reason="stop"
)
```

### Token Counting

**OpenAI:** Use `tiktoken` library
**Anthropic:** Use anthropic tokenizer
**Local models:** Use model-specific tokenizer

Accuracy requirement: ±5% of actual token count

### Error Handling

```python
try:
    response = await client.generate(messages)
except RateLimitError:
    # Wait and retry with exponential backoff
except APIError:
    # Log and raise ModelClientError
except NetworkError:
    # Retry with backoff
except TimeoutError:
    # Raise with context
```

---

## 3. Tool System

### Requirements

**Functional:**
- Define tools with JSON Schema
- Async execution
- Parameter validation
- Error handling with graceful degradation
- Support for required/optional parameters

**Non-Functional:**
- Tool execution timeout: 30s default
- Support for parallel execution
- Isolation (one tool can't affect others)
- Deterministic execution where possible

### Tool Schema Standard

Following OpenAI function calling specification:

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a location. Returns temperature, condition, humidity.",
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

### Tool Execution Contract

**Input:** Validated parameters matching schema
**Output:** JSON string with result or error
**Timeout:** Configurable, default 30s
**Errors:** Caught and returned as structured error

**Success Response:**
```json
{
    "success": true,
    "result": {...},
    "execution_time_ms": 123
}
```

**Error Response:**
```json
{
    "success": false,
    "error": "Connection timeout",
    "error_type": "NetworkError",
    "retryable": true
}
```

### Tool Categories

1. **Information Retrieval**
   - Web search
   - Database queries
   - API calls

2. **Computation**
   - Mathematical calculations
   - Data processing
   - Analysis

3. **Actions**
   - File operations
   - System commands
   - External service calls

4. **Generation**
   - Code generation
   - Content creation
   - Image generation

### Security Considerations

- **Input Validation:** Always validate against schema
- **Sandboxing:** Isolate code execution tools
- **Rate Limiting:** Prevent abuse of expensive tools
- **Authentication:** Secure API keys and credentials
- **Logging:** Audit all tool executions

---

## 4. Memory System

### Requirements

**Functional:**
- Store conversation history
- Retrieve messages with filtering
- Track token usage
- Support persistence
- Enable message search

**Non-Functional:**
- Fast retrieval (< 10ms for 1000 messages)
- Memory efficient storage
- Thread-safe operations
- Scalable to 100K+ messages

### Memory Strategies

#### 1. **UnboundedMemory**
- Stores all messages
- No pruning
- **Use Case:** Development, short conversations
- **Limitation:** Can exceed context window

#### 2. **SlidingWindowMemory** (Future)
```python
memory = SlidingWindowMemory(window_size=20)
# Keeps only last 20 messages
# Maintains important system messages
```

#### 3. **TokenLimitMemory** (Future)
```python
memory = TokenLimitMemory(
    max_tokens=4000,
    model_client=client,  # For token counting
    strategy="recent"  # or "important" or "summarize"
)
```

**Strategies:**
- **recent:** Keep most recent messages
- **important:** Score by importance, keep top K
- **summarize:** Summarize old messages, keep recent

#### 4. **VectorMemory** (Future)
```python
memory = VectorMemory(
    embedding_client=embeddings,
    top_k=5,
    similarity_threshold=0.7
)
# Retrieves semantically similar messages
```

### Persistence Format

**SQLite Storage:**
```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    role TEXT,
    content TEXT,
    tool_calls TEXT,  -- JSON
    timestamp DATETIME,
    metadata TEXT,  -- JSON
    tokens INTEGER
);
```

**JSON Storage:**
```json
{
    "conversation_id": "conv_123",
    "created_at": "2026-01-25T10:00:00Z",
    "messages": [
        {
            "id": "msg_1",
            "role": "user",
            "content": "Hello",
            ...
        }
    ],
    "metadata": {
        "total_tokens": 150,
        "total_messages": 5
    }
}
```

---

## 5. Agent System

### Requirements

**Functional:**
- Autonomous execution loop
- Tool calling with retries
- Memory management
- State persistence
- Streaming support

**Non-Functional:**
- Max iterations: 10 (configurable)
- Timeout: 5 minutes per run
- Concurrent tool execution support
- Resource cleanup on error
- Graceful degradation

### Agent Types

#### 1. **ReActAgent** (Reasoning + Acting)

**Algorithm:**
```
1. Receive user input
2. Add to memory
3. LOOP (max_iterations):
   a. Get messages from memory
   b. Call LLM with tools
   c. If no tool calls:
      - Return response (DONE)
   d. If tool calls:
      - Execute tools (parallel if possible)
      - Add results to memory
      - Continue loop
4. If max iterations reached:
   - Return partial result with warning
```

**Configuration:**
```python
{
    "max_iterations": 10,
    "max_tool_retries": 2,
    "tool_choice": "auto",
    "parallel_tools": true,
    "timeout": 300,  # seconds
    "fallback_response": "I couldn't complete this task"
}
```

#### 2. **ConversationalAgent** (Future)

Simple chat agent without tool loop:
```
User → Memory → LLM → Response → Memory
```

#### 3. **PlannerAgent** (Future)

Multi-step planning:
```
1. Create plan (list of steps)
2. Execute each step
3. Adjust plan based on results
4. Return final result
```

#### 4. **MultiAgent** (Future)

Agent collaboration:
```
Orchestrator Agent
├── Specialist Agent 1
├── Specialist Agent 2
└── Specialist Agent 3
```

### State Management

**Agent State:**
```json
{
    "agent_id": "agent_123",
    "name": "math_assistant",
    "created_at": "2026-01-25T10:00:00Z",
    "configuration": {
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_iterations": 10
    },
    "memory_state": {
        "messages": [...],
        "token_count": 1500
    },
    "metrics": {
        "total_runs": 42,
        "total_tokens": 50000,
        "avg_latency_ms": 2340
    }
}
```

**Checkpoint System:**
```python
# Save checkpoint after each iteration
agent.save_checkpoint("checkpoint_iter_5.json")

# Restore from checkpoint
agent = ReActAgent.load_checkpoint("checkpoint_iter_5.json")
```

---

## 6. Observability System

### Requirements

**Functional:**
- Structured logging
- Distributed tracing
- Metrics collection
- Event hooks/callbacks
- Cost tracking

**Non-Functional:**
- Low overhead (< 5% latency)
- Async logging (non-blocking)
- Configurable verbosity
- Export to multiple backends

### Logging Specification

**Log Levels:**
- `DEBUG`: Detailed diagnostic info
- `INFO`: General informational messages
- `WARNING`: Warning messages
- `ERROR`: Error messages
- `CRITICAL`: Critical failures

**Log Format (JSON):**
```json
{
    "timestamp": "2026-01-25T10:30:00.123Z",
    "level": "INFO",
    "logger": "agent_framework.agents.react",
    "message": "Tool execution completed",
    "context": {
        "agent_id": "agent_123",
        "tool_name": "calculator",
        "execution_time_ms": 45,
        "success": true
    },
    "trace_id": "trace_abc",
    "span_id": "span_xyz"
}
```

### Tracing Specification

**Span Hierarchy:**
```
agent.run [2340ms]
├── memory.get_messages [2ms]
├── model.generate [1800ms]
│   ├── tokenize [10ms]
│   ├── api_call [1750ms]
│   └── parse_response [40ms]
├── tool.execute [500ms]
│   ├── validate_input [2ms]
│   ├── execute_logic [490ms]
│   └── format_output [8ms]
└── memory.add_message [3ms]
```

**Trace Attributes:**
```python
{
    "service.name": "agent_framework",
    "agent.id": "agent_123",
    "agent.name": "math_assistant",
    "model.name": "gpt-4o",
    "model.temperature": 0.7,
    "tokens.prompt": 150,
    "tokens.completion": 50,
    "tokens.total": 200,
    "cost.usd": 0.004,
    "tool.name": "calculator",
    "tool.success": true
}
```

### Metrics Specification

**Key Metrics:**

1. **Latency:**
   - `agent.run.duration` (histogram)
   - `model.generate.duration` (histogram)
   - `tool.execute.duration` (histogram)

2. **Token Usage:**
   - `tokens.prompt` (counter)
   - `tokens.completion` (counter)
   - `tokens.total` (counter)

3. **Cost:**
   - `cost.usd` (counter)
   - `cost.by_model` (counter with model label)

4. **Success Rates:**
   - `agent.run.success` (counter)
   - `tool.execute.success` (counter)
   - `model.generate.success` (counter)

5. **Iterations:**
   - `agent.iterations` (histogram)

### Callback System

```python
class AgentCallback:
    async def on_agent_start(self, agent, input, **kwargs):
        """Called when agent starts."""
    
    async def on_agent_end(self, agent, output, **kwargs):
        """Called when agent completes."""
    
    async def on_agent_error(self, agent, error, **kwargs):
        """Called on error."""
    
    async def on_llm_start(self, model, messages, **kwargs):
        """Called before LLM call."""
    
    async def on_llm_end(self, model, response, **kwargs):
        """Called after LLM call."""
    
    async def on_tool_start(self, tool, inputs, **kwargs):
        """Called before tool execution."""
    
    async def on_tool_end(self, tool, output, **kwargs):
        """Called after tool execution."""
    
    async def on_tool_error(self, tool, error, **kwargs):
        """Called on tool error."""
```

---

## 7. Configuration System

### Requirements

**Functional:**
- Load from multiple sources (env, file, code)
- Type validation
- Secret management
- Environment-specific configs

**Non-Functional:**
- Fail fast on invalid config
- Clear error messages
- Support for nested configs
- Hot reload support (future)

### Configuration Schema

```yaml
# config.yaml
agent:
  name: "my_agent"
  description: "A helpful assistant"
  max_iterations: 10
  timeout: 300
  verbose: true

model:
  provider: "openai"
  model: "gpt-4o"
  temperature: 0.7
  max_tokens: 2000
  api_key: "${OPENAI_API_KEY}"  # From environment

memory:
  type: "token_limit"
  max_tokens: 4000
  strategy: "recent"

tools:
  - name: "calculator"
    enabled: true
  - name: "web_search"
    enabled: false
    config:
      api_key: "${SEARCH_API_KEY}"

observability:
  logging:
    level: "INFO"
    format: "json"
    output: "stdout"
  tracing:
    enabled: true
    endpoint: "http://localhost:14268"
  metrics:
    enabled: true
    port: 9090
```

### Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...

# Optional
AGENT_LOG_LEVEL=INFO
AGENT_MAX_ITERATIONS=10
AGENT_TIMEOUT=300
ENABLE_TRACING=true
TRACE_ENDPOINT=http://jaeger:14268
```

---

## 8. Error Handling

### Error Hierarchy

```
AgentError (base)
├── ConfigurationError
├── ModelClientError
│   ├── RateLimitError
│   ├── AuthenticationError
│   └── InvalidRequestError
├── ToolExecutionError
│   ├── ToolNotFoundError
│   ├── ToolTimeoutError
│   └── ToolValidationError
├── MemoryError
│   ├── MemoryFullError
│   └── PersistenceError
├── MaxIterationsExceeded
└── TokenLimitExceeded
```

### Error Response Format

```json
{
    "success": false,
    "error": {
        "type": "ToolExecutionError",
        "message": "Calculator tool failed: division by zero",
        "code": "TOOL_EXECUTION_ERROR",
        "details": {
            "tool_name": "calculator",
            "input": {"expression": "1/0"},
            "retryable": false
        },
        "timestamp": "2026-01-25T10:30:00Z",
        "trace_id": "trace_abc"
    }
}
```

---

## Performance Targets

| Component | Target | Measurement |
|-----------|--------|-------------|
| Message serialization | < 1ms | Per message |
| Memory retrieval | < 10ms | 1000 messages |
| Tool execution | < 30s | Default timeout |
| LLM call | < 5s | Non-streaming |
| Agent iteration | < 10s | Including tool calls |
| Logging overhead | < 5% | Added latency |
| Token counting | < 5ms | 1000 tokens |

---

## Security Specifications

1. **API Keys:** Never log, always use env vars
2. **Input Validation:** Validate all user inputs
3. **Tool Sandboxing:** Isolate code execution
4. **Rate Limiting:** Implement per-tool limits
5. **Audit Logging:** Log all sensitive operations
6. **Error Messages:** Don't leak sensitive info

---

## Testing Requirements

1. **Unit Tests:** 80%+ coverage
2. **Integration Tests:** All component interactions
3. **E2E Tests:** Full agent workflows
4. **Performance Tests:** Meet performance targets
5. **Security Tests:** Vulnerability scanning
6. **Load Tests:** Handle expected traffic

---

This specification ensures consistency and quality across all components of the framework.
