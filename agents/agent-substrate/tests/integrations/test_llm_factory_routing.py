"""LLMFactory provider routing — which concrete client class each provider
builds. Found with zero test coverage while auditing whether self-hosted
servers (vLLM/SGLang/llama.cpp) actually work through this factory: the
design in ``factory.py``'s own docstring/``_CHAT_COMPLETIONS_PROVIDERS`` was
correct (every self-hosted-friendly provider routes to
``OpenAIChatCompletionClient``, the ``/v1/chat/completions`` client — only
``"openai"`` gets the Responses-API ``OpenAIClient``) but nothing verified
it, so a future refactor could silently break self-hosted-model support
with no test catching it.
"""

from __future__ import annotations

import pytest

from substrate.integrations.llm.factory import (
    LLMFactory,
    create_embedding_client,
    detect_embedding_provider,
    detect_provider,
)


def test_bare_openai_model_name_routes_to_responses_api_client() -> None:
    from substrate.integrations.llm.openai.openai_client import OpenAIClient

    factory = LLMFactory("gpt-4o-mini", "sk-test")
    assert factory.provider == "openai"
    assert isinstance(factory.build(), OpenAIClient)


@pytest.mark.parametrize(
    "model,base_url",
    [
        ("compatible/my-local-model", "http://localhost:9999/v1"),
        ("vllm/mistral-7b", None),  # has a built-in default base_url
        ("ollama/llama3.2", None),
        ("groq/llama-3.3-70b-versatile", None),
    ],
)
def test_self_hosted_and_openai_compatible_providers_route_to_chat_completions_client(
    model: str, base_url: str | None
) -> None:
    """These are exactly the providers a self-hosted deployment (vLLM,
    SGLang, llama.cpp's llama-server) needs to work — they must all build
    the client that speaks ``/v1/chat/completions``, not the one that
    speaks OpenAI's newer Responses API, which self-hosted servers do not
    implement."""
    from substrate.agents.llm.chat_client import OpenAIChatCompletionClient

    factory = LLMFactory(model, "sk-test")
    kwargs = {"base_url": base_url} if base_url else {}
    client = factory.build(**kwargs)

    assert isinstance(client, OpenAIChatCompletionClient)
    # The strict-tool-schema branch in OpenAIChatCompletionClient keys off
    # this tag — a provider must be tagged as itself, not silently left as
    # the client's own "openai"/"compatible" default from __init__.
    assert client.provider == factory.provider


def test_compatible_provider_requires_an_explicit_base_url() -> None:
    """The one provider with no built-in default — omitting base_url must
    fail loudly at build time, not silently hit whatever AsyncOpenAI's own
    default endpoint is."""
    factory = LLMFactory("compatible/my-model", "sk-test")
    with pytest.raises(ValueError, match="requires an explicit base_url"):
        factory.build()


def test_anthropic_and_gemini_route_to_their_own_clients() -> None:
    from substrate.integrations.llm.anthropic.anthropic_client import AnthropicClient
    from substrate.integrations.llm.gemini.gemini_client import GeminiClient

    assert isinstance(
        LLMFactory("claude-sonnet-4-20250514", "sk-ant-test").build(), AnthropicClient
    )
    assert isinstance(LLMFactory("gemini-2.5-flash", "gm-test").build(), GeminiClient)


def test_detect_provider_prefers_explicit_prefix_over_bare_name_heuristics() -> None:
    """A prefixed model always wins over the bare-name startswith() guesses
    below it -- e.g. "openrouter/openai/gpt-4o" must not be misread as
    bare "gpt-4o" and routed to real OpenAI."""
    assert detect_provider("openrouter/openai/gpt-4o") == "openrouter"
    assert detect_provider("gpt-4o-mini") == "openai"


@pytest.mark.parametrize(
    "prefix",
    ["compatible", "vllm", "ollama", "lmstudio", "llamacpp"],
)
def test_embedding_compatible_prefixes_route_to_openai_compatible(prefix: str) -> None:
    """Closes the one real asymmetry between the two factories: chat/VL
    models could already target an arbitrary local/remote OpenAI-compatible
    server via "compatible"/"vllm"; embeddings could not, short of
    smuggling it through provider="openai" + an explicit base_url=."""
    assert detect_embedding_provider(f"{prefix}/my-embed-model") == "openai_compatible"


def test_embedding_compatible_provider_builds_with_base_url() -> None:
    from substrate.integrations.llm.openai.openai_embedding_client import (
        OpenAIEmbeddingClient,
    )

    client = create_embedding_client(
        "compatible/my-embed-model",
        api_key="sk-test",
        base_url="http://localhost:8080/v1",
    )
    assert isinstance(client, OpenAIEmbeddingClient)


def test_embedding_compatible_provider_requires_an_explicit_base_url() -> None:
    with pytest.raises(ValueError, match="requires an explicit base_url"):
        create_embedding_client("compatible/my-embed-model")


def test_existing_embedding_providers_unaffected() -> None:
    """Regression guard: adding the compatible/vllm/etc. branch must not
    change routing for the three providers this factory already handled."""
    assert detect_embedding_provider("openai/text-embedding-3-small") == "openai"
    assert detect_embedding_provider("gemini/text-embedding-004") == "gemini"
    assert (
        detect_embedding_provider("sentence-transformers/all-MiniLM-L6-v2")
        == "sentence_transformers"
    )
