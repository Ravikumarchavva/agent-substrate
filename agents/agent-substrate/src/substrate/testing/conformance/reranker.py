"""Conformance suite for ``Reranker``.

A ``Provider`` builds the reranker around a scripted HTTP handler and says how that service's request carries the passages and its
answer carries scores. The suite supplies the score for each passage (``score_of``) so *order* and *pairing* are checked, not just
shape. What every reranker must do: one score per passage in the passages' order whatever order the service answers in; nothing for
nothing; survive hostile text; and raise — never return zeros or ``None`` — when the service fails or answers something it cannot read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import httpx
import pytest

from substrate.models.protocols import Reranker

Handler = Callable[[httpx.Request], httpx.Response]


def score_of(passage: str) -> float:
    """A deterministic score that differs for different passages."""
    return round((len(passage) * 7 + sum(map(ord, passage)) % 101) / 100, 4)


class Provider(Protocol):
    def reranker(self, handler: Handler) -> Reranker: ...

    def passages_in(self, request: httpx.Request) -> list[str]:
        """The passages the service's request carries, in order."""

    def scores_response(self, scores: list[float], *, shuffled: bool = False) -> httpx.Response:
        """The service's answer for ``scores`` (indexed by passage); ``shuffled`` lists them in another order, as services may."""

    def error_response(self, status: int, message: str) -> httpx.Response: ...

    def garbled_response(self) -> httpx.Response:
        """An answer that is not what the service promises."""


class RerankerConformance:
    @pytest.fixture
    def provider(self) -> Provider:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    def build(self, provider: Provider, *, answer: Callable[[list[str]], httpx.Response] | None = None, fail_with: int | None = None) -> Reranker:
        def handler(request: httpx.Request) -> httpx.Response:
            if fail_with is not None:
                return provider.error_response(fail_with, "provider failure")
            passages = provider.passages_in(request)
            if answer is not None:
                return answer(passages)
            return provider.scores_response([score_of(p) for p in passages])

        return provider.reranker(handler)

    async def test_each_passage_gets_its_own_score_in_order(self, provider: Provider) -> None:
        reranker = self.build(provider)
        passages = ["alpha", "a much longer second passage", "γ", "with  double  spaces"]
        assert await reranker.rerank("a query", passages) == [score_of(p) for p in passages]

    async def test_scores_are_paired_with_passages_whatever_order_the_service_answers_in(self, provider: Provider) -> None:
        reranker = self.build(provider, answer=lambda passages: provider.scores_response([score_of(p) for p in passages], shuffled=True))
        passages = [f"passage number {i}" * (i + 1) for i in range(6)]
        assert await reranker.rerank("q", passages) == [score_of(p) for p in passages]

    async def test_it_names_its_model(self, provider: Provider) -> None:
        assert isinstance(self.build(provider).model, str)

    async def test_reranking_nothing_returns_nothing(self, provider: Provider) -> None:
        assert await self.build(provider).rerank("q", []) == []

    @pytest.mark.parametrize("hostile", ["", " ", "\n\n", "x' OR '1'='1", "a" * 20_000, "ünï-çødé 日本語 🙂", "\x00\x01 control"])
    async def test_hostile_text_is_data_not_a_failure(self, provider: Provider, hostile: str) -> None:
        reranker = self.build(provider)
        scores = await reranker.rerank(hostile, [hostile, "after"])
        assert scores[-1] == score_of("after"), "a hostile text shifted the scores of the passages after it"

    async def test_a_service_failure_raises_instead_of_returning_wrong_scores(self, provider: Provider) -> None:
        with pytest.raises(Exception):
            await self.build(provider, fail_with=500).rerank("q", ["a"])

    async def test_an_answer_it_cannot_read_raises_instead_of_returning_zeros(self, provider: Provider) -> None:
        with pytest.raises(Exception):
            await self.build(provider, answer=lambda passages: provider.garbled_response()).rerank("q", ["a", "b"])


__all__ = ["RerankerConformance", "Provider", "score_of", "Any"]
