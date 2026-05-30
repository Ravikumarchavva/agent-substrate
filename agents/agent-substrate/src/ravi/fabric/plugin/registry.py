"""Plugin registry implementation.

A single in-process registry keyed by ``(category, name)``. Decorators register
*classes* at import time — instances with configuration are constructed and
registered separately via :class:`ravi.fabric.catalog.AgentCatalog`.

Subclass-check on registration is structural: each category's decorator is
bound to a base class (ABC or Protocol) and refuses to register classes that
don't satisfy it. The kernel never names a concrete extension.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Type, TypeVar

from ravi.kernel.plugin.spec import PluginSpec

logger = logging.getLogger(__name__)


class PluginRegistryError(Exception):
    """Raised on plugin registration failures (duplicate name, bad subclass)."""


_T = TypeVar("_T")

# (category, name) -> PluginSpec
_REGISTRY: Dict[tuple[str, str], PluginSpec] = {}


def _make_decorator(
    category: str, *, base: Type[_T]
) -> Callable[[str], Callable[[Type[_T]], Type[_T]]]:
    """Build a ``@register_<category>(name)`` decorator bound to *base*."""

    def decorator(name: str) -> Callable[[Type[_T]], Type[_T]]:
        if not isinstance(name, str) or not name:
            raise PluginRegistryError(
                f"@register_{category}: name must be a non-empty string"
            )

        def wrap(cls: Type[_T]) -> Type[_T]:
            if not (isinstance(cls, type) and issubclass(cls, base)):
                raise PluginRegistryError(
                    f"@register_{category}('{name}'): {cls!r} is not a "
                    f"subclass of {base.__name__}"
                )
            key = (category, name)
            if key in _REGISTRY:
                existing = _REGISTRY[key].cls
                if existing is cls:
                    # Idempotent re-registration (module re-imported in tests)
                    return cls
                raise PluginRegistryError(
                    f"@register_{category}('{name}'): already registered as "
                    f"{existing.__module__}.{existing.__name__}"
                )
            _REGISTRY[key] = PluginSpec(category=category, name=name, cls=cls)
            logger.debug("plugin registered: %s:%s -> %s", category, name, cls)
            return cls

        return wrap

    return decorator


def get_registered(category: str, name: str) -> Type[object]:
    """Return the class registered as ``(category, name)``.

    Raises :class:`PluginRegistryError` if no match is found.
    """
    try:
        return _REGISTRY[(category, name)].cls
    except KeyError as exc:
        available = sorted(n for c, n in _REGISTRY if c == category)
        raise PluginRegistryError(
            f"No {category} registered as '{name}'. "
            f"Known {category}s: {available or '(none)'}"
        ) from exc


def list_registered(category: str) -> List[str]:
    """Return all names registered under *category* (sorted)."""
    return sorted(n for c, n in _REGISTRY if c == category)


def list_categories() -> List[str]:
    """Return all categories that currently hold at least one plugin (sorted)."""
    return sorted({c for c, _ in _REGISTRY})


def unregister(category: str, name: str) -> None:
    """Remove a plugin from the registry.

    Primarily intended for test isolation. Silently no-ops if the plugin
    is not registered, since unregister is most often called in teardown.
    """
    _REGISTRY.pop((category, name), None)


# ---------------------------------------------------------------------------
# Category-bound decorators
# ---------------------------------------------------------------------------
#
# All base classes are kernel-level (kernel → kernel is fine). Agents are
# validated against the kernel ``AgentProtocol`` contract, not the concrete
# ``ActorAgent`` base (which lives in the L1 fabric layer) — the kernel must
# never import fabric.

from ravi.kernel.agents._protocol import AgentProtocol
from ravi.kernel.context.base_context import ModelContext
from ravi.kernel.guardrails.base_guardrail import BaseGuardrail
from ravi.kernel.llm.base_client import BaseModelClient
from ravi.kernel.memory.history_provider import HistoryProvider
from ravi.kernel.middleware.base import BaseMiddleware
from ravi.kernel.tools.base_tool import BaseTool



def _register_agent_decorator(name: str):  # type: ignore[return]
    """Register an agent class — must be an ``ActorAgent`` subclass."""
    if not isinstance(name, str) or not name:
        raise PluginRegistryError(
            "@register_agent: name must be a non-empty string"
        )

    def wrap(cls: type) -> type:
        if not (isinstance(cls, type) and issubclass(cls, AgentProtocol)):
            raise PluginRegistryError(
                f"@register_agent('{name}'): {cls!r} must implement the agent "
                f"contract (on_message/start/stop) — e.g. subclass ActorAgent"
            )
        key = ("agent", name)
        if key in _REGISTRY:
            existing = _REGISTRY[key].cls
            if existing is cls:
                return cls
            raise PluginRegistryError(
                f"@register_agent('{name}'): already registered as "
                f"{existing.__module__}.{existing.__name__}"
            )
        _REGISTRY[key] = PluginSpec(category="agent", name=name, cls=cls)
        logger.debug("plugin registered: agent:%s -> %s", name, cls)
        return cls

    return wrap


register_agent = _register_agent_decorator
register_guardrail = _make_decorator("guardrail", base=BaseGuardrail)
register_middleware = _make_decorator("middleware", base=BaseMiddleware)
register_provider = _make_decorator("provider", base=BaseModelClient)
register_memory = _make_decorator("memory", base=HistoryProvider)
register_context = _make_decorator("context", base=ModelContext)
register_tool = _make_decorator("tool", base=BaseTool)
# ``rag`` has no canonical base class in kernel yet; use ``object`` and let
# the first concrete RAG strategy define a base when it lands in extensions.
register_rag = _make_decorator("rag", base=object)
