from __future__ import annotations

from typing import Callable, Awaitable

from ravi.agents.middleware._contracts import ChatContext
from ravi.exceptions import MiddlewareTermination


class MaxTokenMiddleware:
    """Reject Chat Context that exceeds a token limit."""

    def __init__(
        self,
        *,
        max_tokens: int = 4096,
        model: str = "gpt-4o",
        chars_per_token: float = 4.0,
    ):
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self._model = model
        self._encoding = None
        try:
            import tiktoken

            try:
                self._encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                try:
                    self._encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    pass
        except ImportError:
            pass

    def _count_tokens(self, text: str) -> int:
        if self._encoding is not None:
            return len(self._encoding.encode(text))
        return int(len(text) / self.chars_per_token)

    async def process(
        self, context: ChatContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        # Concatenate text from all messages for a rough input token count
        total_text = ""
        for msg in context.messages:
            total_text += (
                " ".join(b.text for b in msg.content if getattr(b, "text", None)) + " "
            )

        token_count = self._count_tokens(total_text.strip())

        if token_count > self.max_tokens:
            method = "tiktoken" if self._encoding is not None else "estimated"
            raise MiddlewareTermination(
                f"MaxToken: Input too long: {token_count} tokens ({method}) — limit is {self.max_tokens}"
            )

        await call_next()
