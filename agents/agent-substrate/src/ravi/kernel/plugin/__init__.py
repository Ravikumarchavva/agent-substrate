"""Plugin registry — decorator-based discovery for extension classes.

The kernel exposes one decorator per extension category::

    from ravi.kernel.plugin import register_guardrail
    from ravi.kernel.guardrails.base_guardrail import BaseGuardrail

    @register_guardrail("pii")
    class PIIGuardrail(BaseGuardrail):
        ...

Anywhere downstream::

    from ravi.kernel.plugin import get_registered, list_registered

    cls = get_registered("guardrail", "pii")
    names = list_registered("guardrail")  # → ["pii", "content_filter", ...]

The registry is a simple in-process dict keyed by (category, name). It holds
the *class*, not an instance — instance construction with configuration is
the responsibility of :class:`ravi.kernel.agent_catalog.AgentCatalog`.
"""

from __future__ import annotations

from ravi.kernel.plugin.registry import (
    PluginRegistryError,
    get_registered,
    list_categories,
    list_registered,
    register_agent,
    register_context,
    register_guardrail,
    register_memory,
    register_middleware,
    register_provider,
    register_rag,
    register_tool,
    unregister,
)
from ravi.kernel.plugin.spec import PluginSpec

__all__ = [
    "PluginRegistryError",
    "PluginSpec",
    "get_registered",
    "list_categories",
    "list_registered",
    "register_agent",
    "register_context",
    "register_guardrail",
    "register_memory",
    "register_middleware",
    "register_provider",
    "register_rag",
    "register_tool",
    "unregister",
]
