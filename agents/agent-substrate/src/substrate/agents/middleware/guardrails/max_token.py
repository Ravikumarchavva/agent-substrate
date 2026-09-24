from __future__ import annotations

from typing import Callable, Awaitable, ClassVar

from substrate.agents.context.tokens import DEFAULT_CHARS_PER_TOKEN, estimate_tokens
from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.exceptions import MiddlewareTermination
from substrate.kernel.agent.middleware import MiddlewareStage


class MaxTokenMiddleware:
    """Reject a chat call whose input exceeds a token limit.

    Counts everything that goes over the wire — text, tool-call arguments,
    tool results, images and documents (see ``agents/context/tokens.py``) — plus
    the system prompt. It is an estimate, not a tokenizer count.
    """

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.CHAT})

    def __init__(
        self,
        *,
        max_tokens: int = 4096,
        chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    ):
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token

    async def process(
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        token_count = estimate_tokens(context.messages or [], self.chars_per_token)
        token_count += int(len(context.system_instructions or "") / self.chars_per_token)

        if token_count > self.max_tokens:
            raise MiddlewareTermination(
                f"MaxToken: Input too long: ~{token_count} tokens (estimated) — limit is {self.max_tokens}"
            )

        await call_next()
