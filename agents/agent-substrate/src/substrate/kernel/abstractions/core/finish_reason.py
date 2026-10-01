"""Why a model stopped."""

from __future__ import annotations

from enum import StrEnum


class FinishReason(StrEnum):
    """Why the model stopped. Every client reports one.

    The harness cannot tell a finished answer from one cut off at ``max_tokens``
    without it: a truncated reply looks exactly like a complete one, and a
    refusal looks like an answer.
    """

    STOP = "stop"                    # the model finished
    TOOL_CALLS = "tool_calls"        # it stopped to call tools
    LENGTH = "length"                # cut off by max_tokens or the context window
    CONTENT_FILTER = "content_filter"
    REFUSAL = "refusal"              # the model declined to answer
    ERROR = "error"
    OTHER = "other"                  # a provider reason with no equivalent here
    UNSPECIFIED = "unspecified"      # the client did not say — a client bug


__all__ = ["FinishReason"]
