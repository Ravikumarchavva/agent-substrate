"""Conformance suite for ``LLMClient``.

A client is exercised through a **real HTTP transport that is scripted**, not a mocked method: the
vendor's own SDK builds the request and parses the response, so what is checked is what a provider would
actually see and send back. A subclass supplies a ``Provider`` — how to build the client around an
``httpx`` handler, and what that vendor's wire formats look like for the four responses the suite needs.

What every client must do, whichever vendor it fronts:

* answer a prompt, and report why it stopped (a finish reason is never ``UNSPECIFIED``);
* stream the same answer as text deltas ending in one completion;
* never send content outside ``capabilities.input_modalities`` — it is dropped and the model is told;
* turn each failure the provider can report into the typed error the engine acts on: rate limit with its
  ``Retry-After``, bad credentials, context overflow, content filter.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import pytest

from substrate.kernel.abstractions.core.content import ChatMessage, MediaBlock, Role, TextBlock
from substrate.kernel.abstractions.core.finish_reason import FinishReason
from substrate.kernel.abstractions.exceptions import (
    AuthError,
    ContentFilterError,
    ContextLengthError,
    RateLimitedError,
)
from substrate.kernel.abstractions.llm import LLMClient, ModelCapabilities
from substrate.kernel.abstractions.messaging.stream import CompletionEvent, TextDelta
from substrate.kernel.llm.errors import classify_llm_error

Handler = Callable[[httpx.Request], httpx.Response]


class Provider(Protocol):
    """What a vendor must say about itself for the suite to drive its client."""

    def client(self, handler: Handler, *, capabilities: ModelCapabilities | None = None) -> LLMClient: ...

    def text_response(self, text: str) -> httpx.Response:
        """A finished, non-streaming answer containing exactly ``text``."""

    def stream_response(self, text: str) -> httpx.Response:
        """The same answer as a stream, in the vendor's wire format."""

    def truncated_response(self, text: str) -> httpx.Response:
        """An answer cut off at the token limit."""

    def error_response(self, status: int, message: str, *, retry_after: str | None = None) -> httpx.Response: ...


@dataclass
class Wire:
    """What the provider received."""

    bodies: list[str] = field(default_factory=list)

    @property
    def last(self) -> str:
        return self.bodies[-1]


PNG = b"\x89PNG\r\n\x1a\n" + b"conformance-image-bytes" * 8
PNG_B64 = base64.b64encode(PNG).decode()


def user(*blocks: Any) -> list[ChatMessage]:
    return [ChatMessage(role=Role.USER, content=list(blocks))]


TEXT_ONLY = ModelCapabilities(model_id="conformance-text", input_modalities=frozenset({"text"}))


class LLMClientConformance:
    @pytest.fixture
    def provider(self) -> Provider:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    def build(self, provider: Provider, respond: Callable[[], httpx.Response], *, capabilities: ModelCapabilities | None = None) -> tuple[LLMClient, Wire]:
        wire = Wire()

        def handler(request: httpx.Request) -> httpx.Response:
            wire.bodies.append(request.content.decode("utf-8", errors="replace"))
            return respond()

        return provider.client(handler, capabilities=capabilities), wire

    # ===================================================================== shape

    async def test_a_client_names_its_model_and_declares_capabilities(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.text_response("x"))
        assert isinstance(client.model, str) and client.model
        assert isinstance(client.capabilities, ModelCapabilities)

    async def test_the_prompt_reaches_the_provider(self, provider: Provider) -> None:
        client, wire = self.build(provider, lambda: provider.text_response("pong"))
        await client.generate(user(TextBlock(text="distinctive-prompt-marker")))
        assert "distinctive-prompt-marker" in wire.last

    # ===================================================================== answers

    async def test_generate_returns_the_answer_usage_and_a_stop_reason(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.text_response("the answer"))
        response = await client.generate(user(TextBlock(text="q")))
        assert response.text == "the answer"
        assert response.finish_reason is FinishReason.STOP
        assert response.usage.input_tokens >= 0 and response.usage.output_tokens >= 0

    async def test_a_reply_cut_off_at_the_token_limit_says_so(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.truncated_response("half an ans"))
        response = await client.generate(user(TextBlock(text="q")))
        assert response.finish_reason is FinishReason.LENGTH, "a truncated answer looked exactly like a finished one"

    async def test_streaming_yields_text_deltas_then_one_completion_with_the_whole_answer(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.stream_response("streamed answer"))
        events = [e async for e in client.generate_stream(user(TextBlock(text="q")))]
        deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
        done = [e for e in events if isinstance(e, CompletionEvent)]
        assert len(done) == 1 and events[-1] is done[0], "the stream must end in exactly one completion"
        assert deltas == "streamed answer"
        assert "".join(b.text for b in done[0].content if isinstance(b, TextBlock)) == "streamed answer"
        assert done[0].finish_reason is FinishReason.STOP

    async def test_count_tokens_is_a_non_negative_int(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.text_response("x"))
        n = await client.count_tokens(user(TextBlock(text="some words to count")))
        assert isinstance(n, int) and n >= 0

    # ===================================================================== modalities

    async def test_i16_content_the_model_cannot_see_is_never_sent_and_the_model_is_told(self, provider: Provider) -> None:
        client, wire = self.build(provider, lambda: provider.text_response("ok"), capabilities=TEXT_ONLY)
        await client.generate(user(TextBlock(text="look at this"), MediaBlock.image(data=PNG, media_type="image/png")))
        assert PNG_B64 not in wire.last and "conformance-image-bytes" not in wire.last, "image bytes reached a text-only model"
        assert "look at this" in wire.last
        assert "image" in wire.last.lower(), "the model must be told an image was dropped"

    # ===================================================================== typed failures

    async def test_a_rate_limit_carries_the_providers_retry_after(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.error_response(429, "slow down", retry_after="7"))
        with pytest.raises(Exception) as raised:
            await client.generate(user(TextBlock(text="q")))
        error = classify_llm_error(raised.value)  # type: ignore[arg-type]
        assert isinstance(error, RateLimitedError) and error.retry_after == 7.0

    async def test_bad_credentials_are_an_auth_error(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.error_response(401, "invalid api key"))
        with pytest.raises(Exception) as raised:
            await client.generate(user(TextBlock(text="q")))
        assert isinstance(classify_llm_error(raised.value), AuthError)  # type: ignore[arg-type]

    async def test_a_context_overflow_is_distinguishable_from_any_other_bad_request(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.error_response(400, "This model's maximum context length is 8192 tokens, prompt is too long"))
        with pytest.raises(Exception) as raised:
            await client.generate(user(TextBlock(text="q")))
        assert isinstance(classify_llm_error(raised.value), ContextLengthError)  # type: ignore[arg-type]

    async def test_a_content_policy_refusal_is_a_content_filter_error(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.error_response(400, "blocked by content management policy"))
        with pytest.raises(Exception) as raised:
            await client.generate(user(TextBlock(text="q")))
        assert isinstance(classify_llm_error(raised.value), ContentFilterError)  # type: ignore[arg-type]

    async def test_a_server_error_stays_transient_so_the_run_retries(self, provider: Provider) -> None:
        client, _ = self.build(provider, lambda: provider.error_response(503, "upstream unavailable"))
        with pytest.raises(Exception) as raised:
            await client.generate(user(TextBlock(text="q")))
        error = classify_llm_error(raised.value)  # type: ignore[arg-type]
        assert getattr(error, "retryable", True) is not False, "a 503 must not be classified as permanent"


__all__ = ["LLMClientConformance", "Provider", "Wire", "PNG", "TEXT_ONLY"]
