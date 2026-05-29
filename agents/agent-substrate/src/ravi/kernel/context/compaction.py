"""CompactionStrategy — typed, trigger-aware context compaction contract.

Replaces the single-strategy ``ModelContext`` ABC with a richer abstraction
that declares *when* it fires (``trigger``) and *what* it does (``apply``).

Concrete strategies live in :mod:`ravi.reasoning.memory.context`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, List, Optional

from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient


class Trigger(str, Enum):
    """When a compaction strategy fires in the agent loop."""

    BEFORE_LLM_CALL = "before_llm_call"  # applied every time context is built
    AFTER_RUN = "after_run"              # applied when the agent finishes a run
    ON_MAX_CONTEXT = "on_max_context"    # applied when context budget is exceeded


class CompactionStrategy(ABC):
    """Abstract base for all context compaction strategies.

    Subclasses must declare a class-level ``trigger`` and implement ``apply``.

    Usage::

        class SlidingWindowStrategy(CompactionStrategy):
            trigger = Trigger.BEFORE_LLM_CALL

            def __init__(self, max_messages: int = 40) -> None:
                self.max_messages = max_messages

            async def apply(self, messages, session_id, history, model_client=None):
                ...
    """

    trigger: Trigger  # class-level declaration enforced by __init_subclass__

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if ABC in cls.__bases__:
            return
        if not hasattr(cls, "trigger") or cls.__dict__.get("trigger") is None:
            raise TypeError(
                f"{cls.__qualname__} must declare a class-level `trigger` attribute "
                "(e.g. `trigger = Trigger.BEFORE_LLM_CALL`)."
            )

    @abstractmethod
    async def apply(
        self,
        messages: List[BaseClientMessage],
        session_id: str,
        history: "HistoryProvider",
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        """Return the compacted message list.

        Args:
            messages:     The full unfiltered message list from memory.
            session_id:   Current conversation/session identifier.
            history:      The backing history provider (for strategies that
                          need to rewrite persisted history, e.g. summarising).
            model_client: Optional model client for token-counting strategies.

        Returns:
            An ordered list of ``BaseClientMessage`` objects ready to be sent
            to the model. Must always preserve the SystemMessage if present.
        """
        ...

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__}(trigger={self.trigger.value})>"
