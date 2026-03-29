"""StructuredRouter — deterministic multi-agent dispatch via structured outputs.

Uses a typed LLM decision to route incoming messages to the correct
sub-agent or handler — without relying on free-form tool-calling or
fragile string matching.

Typical pattern (house-broker dispatch):

    from pydantic import BaseModel
    from raavan.core.structured import StructuredRouter

    class HouseCategory(BaseModel):
        category: str       # 'furnish' | 'repair' | 'clear_sky_photo'
        reasoning: str

    router = StructuredRouter(
        client=openai_client,
        routing_schema=HouseCategory,
        routing_key='category',
        routes={
            'furnish':         furnish_agent,
            'repair':          repair_agent,
            'clear_sky_photo': clear_sky_agent,
        },
        system_prompt=(
            'You are a house-brokerage coordinator. '
            'Classify the incoming request into one of: '
            'furnish, repair, clear_sky_photo.'
        ),
    )

    decision, result = await router.route(messages)
    print(decision.parsed.category, decision.parsed.reasoning)

The ``category`` field is parsed deterministically — the model is forced
into the declared schema and cannot invent new values, making the
dispatch entirely predictable.
"""

from __future__ import annotations

import logging
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
    TYPE_CHECKING,
)

from pydantic import BaseModel

from raavan.core.structured.parse import parse
from raavan.core.structured.result import (
    StructuredOutputError,
    StructuredOutputResult,
)

if TYPE_CHECKING:
    from raavan.core.agents.base_agent import BaseAgent
    from raavan.core.messages.base_message import BaseClientMessage
    from raavan.integrations.llm.base_client import BaseModelClient

logger = logging.getLogger("raavan.structured.router")

# A route target is either a BaseAgent or any async/sync callable
RouteTarget = Union["BaseAgent", Callable[..., Any]]


class StructuredRouter:
    """Deterministic LLM-driven multi-agent dispatcher.

    Makes *one* structured output call to a routing model, reads a field
    from the typed decision, and dispatches to the matching sub-agent or
    handler.  Because the routing decision is a validated Pydantic value,
    the dispatch is deterministic and type-safe.

    Args:
        client: Model client used for the routing decision call.
        routing_schema: A Pydantic ``BaseModel`` subclass whose field
            ``routing_key`` provides the dispatch value.
        routing_key: Field name on ``routing_schema`` to read the dispatch
            value from.  The value is cast to ``str`` and looked up in
            ``routes``.
        routes: Mapping of dispatch-value → ``BaseAgent | Callable``.
            If the target is a ``BaseAgent`` that has a ``run()`` method,
            it is called as ``await target.run(input_text)``.
            If the target is a coroutine function, it is called as
            ``await target(input_text, decision)``.
            If the target is a plain callable, it is called as
            ``target(input_text, decision)``.
        system_prompt: System instructions for the routing model.  Include
            the list of valid categories so the model stays on-schema.
        fallback: Optional default target to invoke when the routing value
            from the model does not match any key in ``routes``.  If
            ``None`` and no match is found, ``StructuredOutputError`` is
            raised.

    Example::

        class Priority(BaseModel):
            level: str          # 'high' | 'medium' | 'low'
            reasoning: str

        ticket_router = StructuredRouter(
            client=client,
            routing_schema=Priority,
            routing_key='level',
            routes={
                'high':   urgent_support_agent,
                'medium': standard_support_agent,
                'low':    self_serve_agent,
            },
            system_prompt='Classify support ticket priority: high, medium, or low.',
        )

        decision, answer = await ticket_router.route(messages)
    """

    def __init__(
        self,
        client: "BaseModelClient",
        routing_schema: Type[BaseModel],
        routing_key: str,
        routes: Dict[str, RouteTarget],
        system_prompt: str,
        *,
        fallback: Optional[RouteTarget] = None,
    ) -> None:
        self._client = client
        self._routing_schema = routing_schema
        self._routing_key = routing_key
        self._routes = routes
        self._system_prompt = system_prompt
        self._fallback = fallback

    async def route(
        self,
        messages: "List[BaseClientMessage]",
        *,
        input_text: Optional[str] = None,
    ) -> Tuple[StructuredOutputResult, Any]:
        """Make a routing decision and dispatch to the matching sub-agent.

        Args:
            messages: Conversation context passed to the routing model.
            input_text: Optional override of the plain-text input forwarded
                to the sub-agent ``run()`` call.  If omitted, the text
                content of the *last user message* in ``messages`` is used.

        Returns:
            ``(decision, sub_result)`` where ``decision`` is the typed
            routing result and ``sub_result`` is whatever the sub-agent /
            callable returned.

        Raises:
            StructuredOutputError: If the routing model refused or the
                parsed routing key does not match any route and no
                ``fallback`` is configured.
        """
        # --- Step 1: get deterministic routing decision -------------------
        decision: StructuredOutputResult = await parse(
            client=self._client,
            messages=messages,
            schema=self._routing_schema,
            system=self._system_prompt,
        )

        if decision.refused:
            raise StructuredOutputError(
                f"StructuredRouter: routing model refused. {decision.refusal}"
            )

        routing_value = str(getattr(decision.parsed, self._routing_key, ""))
        logger.info(
            "StructuredRouter: %s=%r  (schema=%s)",
            self._routing_key,
            routing_value,
            self._routing_schema.__name__,
        )

        # --- Step 2: resolve route target ---------------------------------
        target = self._routes.get(routing_value) or self._fallback
        if target is None:
            known = list(self._routes.keys())
            raise StructuredOutputError(
                f"StructuredRouter: no route for {self._routing_key!r}={routing_value!r}. "
                f"Known routes: {known}"
            )

        # --- Step 3: extract plain-text input for sub-agent ---------------
        if input_text is None:
            input_text = _extract_last_user_text(messages)

        # --- Step 4: dispatch to sub-agent or callable --------------------
        import inspect

        sub_result: Any
        if hasattr(target, "run"):
            # BaseAgent duck-type: call run(input_text)
            sub_result = await target.run(input_text)  # type: ignore[union-attr]
        elif inspect.iscoroutinefunction(target):
            sub_result = await target(input_text, decision)  # type: ignore[operator]
        else:
            sub_result = target(input_text, decision)  # type: ignore[operator]

        return decision, sub_result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_last_user_text(messages: "List[BaseClientMessage]") -> str:
    """Pull the plain text from the last user / system message."""
    for msg in reversed(messages):
        if msg.role in ("user", "system"):
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        return part
                    if isinstance(part, dict) and part.get("type") == "text":
                        return part.get("text", "")
    return ""
