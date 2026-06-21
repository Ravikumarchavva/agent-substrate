# llm/ — LLM & Embedding Contracts

> **Source:** `kernel/llm/llm.py`

Defines two Protocols: one for text generation, one for embeddings. Every LLM adapter in `integrations/llm/` implements `LLMClient`. The kernel never imports a concrete client — it only defines what every client must look like.

---

## Protocol Overview

## Protocol Overview

The LLM subpackage defines the contracts for communicating with foundation models:

### Protocols & Core Methods

| Protocol | Method / Property | Description | Return Type |
|---|---|---|---|
| **`LLMClient`** | `model: str` (Property) | Model identifier (e.g. `gpt-4o`). | `str` |
| | `generate(messages, options, ctx)` | Synchronously await the model completion. | `LLMResponse` |
| | `generate_stream(messages, options, ctx)` | Stream response tokens (text or reasoning chunks). | `AsyncIterator[TokenStreamEvent]` |
| | `count_tokens(messages)` | Count the tokens for a set of messages. | `int` |
| **`EmbeddingClient`** | `embed(texts)` | Generate embeddings for a list of texts in bulk. | `EmbeddingResult` |
| | `embed_single(text)` | Generate an embedding vector for a single text. | `list[float]` |

### Data Structures

| Class | Fields | Purpose |
|---|---|---|
| `GenerationOptions` | `tools: list[AnyTool] \| None`<br>`system_instructions: str`<br>`temperature: float \| None`<br>`max_tokens: int \| None`<br>`tool_choice: str \| dict \| None`<br>`response_format: type[BaseModel] \| None`<br>`stop: list[str] \| None` | Strongly-typed configuration options for text generation. Replaces generic `**kwargs`. |
| `LLMResponse` | `content: list[ContentBlock]`<br>`usage: Usage` | Successful text generation output holding content blocks and token usage stats. |
| `EmbeddingResult` | `embeddings: list[list[float]]`<br>`model: str`<br>`usage_tokens: int` | Embedding generation outputs mapping input texts to vectors with model details. |

---

## Token Stream Events

When `generate_stream` is called, it returns an `AsyncIterator` that yields events in this order:

```mermaid
sequenceDiagram
    autonumber
    participant Agent
    participant LLMClient
    participant Provider as LLM Provider API

    Agent->>LLMClient: generate_stream(messages, options)
    LLMClient->>Provider: HTTP POST /v1/chat/completions (stream=true)

    loop Token streaming
        Provider-->>LLMClient: SSE chunk
        alt text token
            LLMClient-->>Agent: TextDelta(text, seq)
        else reasoning token
            LLMClient-->>Agent: ReasoningDelta(text, seq)
        end
    end

    Provider-->>LLMClient: [DONE]
    LLMClient-->>Agent: CompletionEvent(content, usage, seq)

    Note over Agent: Agent assembles final content from CompletionEvent.content
```

### Event types

| Event | When | Key fields |
|---|---|---|
| `TextDelta` | Each text token | `text`, `seq`, `run_id`, `agent_id` |
| `ReasoningDelta` | Each thinking token (extended-thinking models only) | `text`, `seq` |
| `CompletionEvent` | End of stream | `content: list[ContentBlock]`, `usage: Usage`, `seq` |

`seq` is strictly increasing within one run. Consumers use it to reorder out-of-order delivery from pub/sub transports.

---

## GenerationOptions — Typed Parameters

`GenerationOptions` replaces `**kwargs` in both `generate` and `generate_stream`. Every implementation agrees on the same parameter names — no silent mismatch possible.

`tools: list[AnyTool]` — the kernel contract. Each LLM adapter converts to its vendor wire format internally (OpenAI `function` objects, Anthropic `tools` array, etc.). The kernel never inspects vendor wire formats.

---

## Concrete Adapters (at L2 and L1)

| Class | Layer | Notes |
|---|---|---|
| `OpenAIChatCompletionClient` | L2 `capabilities/llm/` | Universal `/v1/chat/completions` client — works with OpenAI, Groq, Ollama, any OpenAI-compatible endpoint |
| `FallbackClient` | L1 `agents/llm/` | Wraps multiple `LLMClient` instances; tries each in order on failure |
| `SemanticCache` | L1 `agents/llm/` | Wraps an `LLMClient`; returns cached responses for semantically similar prompts |
| `LLMFactory` | `integrations/llm/` | Auto-detects provider from model name prefix; builds the correct adapter |

Build via `LLMFactory`:

```python
from agent_substrate.integrations.llm import LLMFactory

client = LLMFactory("gpt-4o", api_key).build()                        # OpenAI
client = LLMFactory("anthropic/claude-opus-4-8", api_key).build()     # Anthropic
client = LLMFactory("groq/llama-3.3-70b-versatile", api_key).build()  # Groq
client = LLMFactory("ollama/llama3.2", "ollama").build()               # local
```
