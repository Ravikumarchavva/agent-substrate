# llm/ — LLM & Embedding Contracts

> **Source:** `kernel/llm/llm.py`

Defines two Protocols: one for text generation, one for embeddings. Every LLM adapter in `integrations/llm/` implements `LLMClient`. The kernel never imports a concrete client — it only defines what every client must look like.

---

## Protocol Overview

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef protocol fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef dataobj fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef stream fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef impl fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C,stroke-dasharray:4 2

    LC["LLMClient\n(Protocol)\nmodel: str"]:::protocol
    EC["EmbeddingClient\n(Protocol)"]:::protocol

    GO["GenerationOptions (frozen)\ntools: list[AnyTool] | None\nsystem_instructions: str\ntemperature: float | None\nmax_tokens: int | None\ntool_choice: str | dict | None\nresponse_format: type[BaseModel] | None\nstop: list[str] | None"]:::dataobj

    LR["LLMResponse (frozen)\ncontent: list[ContentBlock]\nusage: Usage"]:::dataobj

    ER["EmbeddingResult (frozen)\nembeddings: list[list[float]]\nmodel: str\nusage_tokens: int"]:::dataobj

    TD["TextDelta\ntoken-by-token text"]:::stream
    RD["ReasoningDelta\nchain-of-thought chunks"]:::stream
    CE["CompletionEvent\nfull response + usage"]:::stream

    OAI["OpenAIChatCompletionClient\n(L2 — capabilities/llm/)"]:::impl
    FALL["FallbackClient\n(L1 — agents/llm/)"]:::impl

    LC -->|"generate(messages, options, ctx)"| LR
    LC -->|"generate_stream(messages, options, ctx)"| TD
    LC -->|"generate_stream(messages, options, ctx)"| RD
    LC -->|"generate_stream(messages, options, ctx)"| CE
    LC -->|"count_tokens(messages)"| CNT["int"]:::dataobj
    LC --> GO
    EC -->|"embed(texts)"| ER
    EC -->|"embed_single(text)"| EMB["list[float]"]:::dataobj

    OAI -.->|"implements"| LC
    FALL -.->|"implements"| LC
```

---

## Token Stream Events

When `generate_stream` is called, it returns an `AsyncIterator` that yields events in this order:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant Agent
    participant LLMClient
    participant Provider as "LLM Provider API"

    Agent->>+LLMClient: generate_stream(messages, options)
    LLMClient->>+Provider: HTTP POST /v1/chat/completions (stream=true)

    loop Token streaming
        Provider-->>LLMClient: SSE chunk
        alt text token
            LLMClient-->>Agent: TextDelta(text, seq)
        else reasoning token
            LLMClient-->>Agent: ReasoningDelta(text, seq)
        end
    end

    Provider-->>-LLMClient: [DONE]
    LLMClient-->>-Agent: CompletionEvent(content, usage, seq)

    Note over Agent: Agent assembles final content<br/>from CompletionEvent.content
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
from ravi.integrations.llm import LLMFactory

client = LLMFactory("gpt-4o", api_key).build()                        # OpenAI
client = LLMFactory("anthropic/claude-opus-4-8", api_key).build()     # Anthropic
client = LLMFactory("groq/llama-3.3-70b-versatile", api_key).build()  # Groq
client = LLMFactory("ollama/llama3.2", "ollama").build()               # local
```
