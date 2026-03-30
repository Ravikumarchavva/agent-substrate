"""ModelContext ABC — builds the message list passed to every LLM call.

A ``ModelContext`` sits between raw memory and the model client.  Before each
LLM invocation the agent calls ``await context.build(...)`` which may:

* Trim the history to fit a token budget.
* Splice in long-term memory retrieved from Postgres / pgvector.
* Apply a sliding-window strategy.
* Fuse hot (Redis) and cold (Postgres) memory tiers.

The contract is deliberately minimal so that any strategy can be dropped in
without modifying the agent loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

from raavan.core.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from raavan.core.llm.base_client import BaseModelClient


class ModelContext(ABC):
    """Abstract base for all context-building strategies.

    Parameters passed to ``build`` are a *superset* of what most strategies
    will need — implementations should simply ignore parameters they do not use.
    """

    @abstractmethod
    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        """Return the final ordered message list for the next LLM call.

        Args:
            session_id:    Identifier for the current conversation/session.
            current_input: The latest user message text (informational; the
                           message itself is already appended to
                           ``raw_messages`` by the time this is called).
            raw_messages:  The complete unfiltered message list from memory.
            model_client:  Optional reference to the model client — useful for
                           token-counting strategies that need the model's
                           tokeniser.

        Returns:
            An ordered list of ``BaseClientMessage`` objects ready to be sent
            to the model.  Must always begin with the SystemMessage (if one
            exists in raw_messages) so that the model's persona is preserved.
        """
        ...

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__}>"
