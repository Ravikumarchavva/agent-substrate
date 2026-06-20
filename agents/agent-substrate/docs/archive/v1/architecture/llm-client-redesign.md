# LLM Client Architecture Redesign

## Problem Statement

Our current multi-LLM implementation creates separate client classes (OpenAIClient, AnthropicClient, GeminiClient) behind a simple `create_model_client()` factory. While this works, it has critical gaps:

1. **No `base_url` support** — `OpenAIClient` hardcodes `AsyncOpenAI(api_key=api_key)` with no `base_url`, making it impossible to use vLLM, Ollama, Together, Perplexity, or any OpenAI-compatible provider.

2. **No model metadata registry** — No way to programmatically query a model's context window, supported modalities, cost per token, or capabilities (thinking, vision, audio). Users and the framework itself are blind to model differences.

3. **No `ProviderConfig` concept** — The factory only forwards `api_key`. There's no structured way to pass `base_url`, `extra_headers`, `organization`, `timeout`, etc.

4. **Provider-specific features preserved but undiscoverable** — OpenAI uses Responses API, Anthropic has extended thinking + prompt caching, Gemini has different content formats. These work but nothing advertises their existence.

---

## Research: Pydantic AI's Three-Tier Architecture

Pydantic AI (the most mature framework for this) uses a **Model + Provider + Profile** separation:

| Concept | Purpose | Example |
|---------|---------|---------|
| **Model** | SDK wrapper class | `OpenAIChatModel`, `AnthropicModel`, `GoogleModel` |
| **Provider** | Auth + connection (base_url, api_key, http_client) | `OpenAIProvider`, `OllamaProvider`, `AzureProvider` |
| **Profile** | Model capabilities + quirks | `ModelProfile(supports_tools=True, supports_thinking=False, ...)` |

Key insight: **Provider is separate from Model**. The same `OpenAIChatModel` works with `OpenAIProvider`, `OllamaProvider`, `AzureProvider`, `DeepSeekProvider`, `PerplexityProvider`, etc. — they just set different `base_url` values.

Their `ModelProfile` is a rich dataclass with ~20 fields covering capabilities, supported output modes, thinking configuration, JSON schema restrictions, etc. Each provider comes with auto-profile functions (`openai_model_profile()`, `anthropic_model_profile()`) that return the right profile for a model name.

---

## Our Adapted Design

We adapt Pydantic AI's pattern but keep it much simpler (3 providers, not 15+):

### New Files

```
src/ravi/core/llm/
├── base_client.py          ← existing (unchanged)
├── models.py               ← NEW: ModelProfile + MODEL_REGISTRY
└── provider.py             ← NEW: ProviderConfig dataclass

src/ravi/integrations/llm/
├── factory.py              ← MODIFIED: accept ProviderConfig
├── openai/openai_client.py ← MODIFIED: accept base_url
├── anthropic/              ← unchanged
└── gemini/                 ← unchanged
```

---

## Phase A: ModelProfile — Model Metadata Registry

**File:** `src/ravi/core/llm/models.py`

```python
@dataclass(frozen=True)
class ModelProfile:
    """Metadata about a specific LLM model."""
    name: str
    provider: str                    # "openai" | "anthropic" | "gemini"
    context_length: int              # max input tokens
    max_output_tokens: int           # max output tokens
    input_cost_per_mtok: float       # $ per 1M input tokens
    output_cost_per_mtok: float      # $ per 1M output tokens
    supports_vision: bool = False
    supports_tools: bool = True
    supports_structured_output: bool = True
    supports_streaming: bool = True
    supports_thinking: bool = False
    thinking_always_on: bool = False  # o1/o3 models
    supports_audio_input: bool = False
    supports_audio_output: bool = False
    supports_image_generation: bool = False
    supports_prompt_caching: bool = False
    modalities: tuple[str, ...] = ("text",)
    aliases: tuple[str, ...] = ()    # alternative names

# Lookup functions
def get_model_profile(model: str) -> ModelProfile | None
def get_context_length(model: str) -> int
def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float
```

**Registry entries (examples):**
- `gpt-5-mini`: 128k context, $0.15/$0.60 per Mtok, vision, tools, structured, audio I/O
- `gpt-4o`: 128k, $2.50/$10.00, vision, audio, thinking
- `o3-mini`: 200k, $1.10/$4.40, thinking always on, no vision
- `claude-sonnet-4-20250514`: 200k, $3/$15, vision, tools, thinking, prompt caching
- `gemini-2.5-flash`: 1M context, $0.075/$0.30, vision, tools, thinking

---

## Phase B: ProviderConfig

**File:** `src/ravi/core/llm/provider.py`

```python
@dataclass
class ProviderConfig:
    """Connection configuration for an LLM provider."""
    provider: str               # "openai" | "anthropic" | "gemini"
    api_key: str | None = None
    base_url: str | None = None  # for OpenAI-compatible endpoints
    organization: str | None = None
    extra_headers: dict[str, str] | None = None
    timeout: float | None = None
```

This replaces the scattered `api_keys` dict. The factory can accept either a `ProviderConfig` or the existing `api_keys` dict for backward compatibility.

---

## Phase C: base_url Support in OpenAIClient

**Minimal change:**

```python
# Before:
self.client = AsyncOpenAI(api_key=api_key)

# After:
client_kwargs: dict[str, Any] = {}
if api_key:
    client_kwargs["api_key"] = api_key
if base_url:
    client_kwargs["base_url"] = base_url
if organization:
    client_kwargs["organization"] = organization
if timeout:
    client_kwargs["timeout"] = timeout
self.client = AsyncOpenAI(**client_kwargs)
```

This single change unlocks vLLM, Ollama, Together, Perplexity, and any OpenAI-compatible provider.

---

## Phase D: Factory Upgrade

The factory gains a `provider_config` parameter:

```python
def create_model_client(
    model: str,
    *,
    provider_config: ProviderConfig | None = None,
    api_keys: dict[str, str] | None = None,  # backward compat
    temperature: float = 0.7,
    max_tokens: int | None = None,
    **kwargs,
) -> BaseModelClient:
```

When `provider_config` is provided, it takes precedence. When only `api_keys` is provided, the factory constructs a basic config from it. This preserves all existing call sites.

---

## Phase E: Settings & App.py

Add to settings:
```python
OPENAI_BASE_URL: str = ""  # empty = default OpenAI API
```

Wire in app.py:
```python
app.state.model_client = create_model_client(
    settings.CHAT_MODEL,
    provider_config=ProviderConfig(
        provider=detect_provider(settings.CHAT_MODEL),
        api_key=api_keys.get(provider),
        base_url=settings.OPENAI_BASE_URL or None,
    ),
)
```

---

## What We Preserve (No Feature Loss)

| Feature | Provider | Status |
|---------|----------|--------|
| Responses API | OpenAI | ✅ Preserved (only OpenAIClient uses it) |
| Extended Thinking | Anthropic | ✅ Preserved (AnthropicClient handles thinking blocks) |
| Prompt Caching | Anthropic | ✅ Preserved (cache_control on system messages) |
| STT/TTS/S2S | OpenAI | ✅ Preserved (optional methods on BaseModelClient) |
| Image Generation | OpenAI | ✅ Preserved (optional method) |
| Tool Calling | All | ✅ Preserved (each client normalizes to native format) |
| Structured Output | All | ✅ Preserved (Pydantic response_format) |
| Streaming | All | ✅ Preserved (TextDeltaChunk / CompletionChunk) |

---

## Implementation Order

1. **Phase A**: `core/llm/models.py` — ModelProfile + registry (standalone, no deps)
2. **Phase B**: `core/llm/provider.py` — ProviderConfig (standalone, no deps)
3. **Phase C**: OpenAIClient `base_url` support (depends on B)
4. **Phase D**: Factory upgrade (depends on A + B + C)
5. **Phase E**: Settings + app.py (depends on D)
