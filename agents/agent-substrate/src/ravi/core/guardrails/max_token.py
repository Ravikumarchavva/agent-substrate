from __future__ import annotations

from ravi.core.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)


class MaxTokenGuardrail(BaseGuardrail):
    """Reject input that exceeds a token limit.

    Uses tiktoken for accurate token counting when available, falling back
    to a chars-per-token ratio so the guardrail works without tiktoken.

    Args:
        max_tokens:       Maximum allowed input tokens (default: 4096).
        model:            Tiktoken encoding model name (default: "gpt-4o").
        chars_per_token:  Fallback chars-per-token ratio (default: 4.0).
        tripwire:         Hard-stop when limit is exceeded (default: True).
    """

    name = "max_token"
    description = "Rejects input exceeding configurable token limit (tiktoken-accurate)"
    guardrail_type = GuardrailType.INPUT

    def __init__(
        self,
        *,
        max_tokens: int = 4096,
        model: str = "gpt-4o",
        chars_per_token: float = 4.0,
        tripwire: bool = True,
    ):
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self.tripwire = tripwire
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

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = ctx.input_text or ""
        token_count = self._count_tokens(text)
        method = "tiktoken" if self._encoding is not None else "estimated"

        if token_count > self.max_tokens:
            return self._fail(
                f"Input too long: {token_count} tokens ({method}) — limit is {self.max_tokens}",
                tripwire=self.tripwire,
                token_count=token_count,
                max_tokens=self.max_tokens,
                counting_method=method,
            )

        return self._pass(
            f"Token count OK: {token_count}/{self.max_tokens} ({method})",
            token_count=token_count,
            max_tokens=self.max_tokens,
            counting_method=method,
        )
