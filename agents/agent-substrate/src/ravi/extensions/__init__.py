"""Built-in extensions over ``ravi.kernel``.

This package holds all *feature implementations*: concrete agents,
guardrails, middleware, RAG strategies, structured-output parsers,
extraction helpers, batch processors, pipeline runners, etc.

The kernel never imports from here — the layering is strictly downward.
To make every built-in extension discoverable, import this package once
at application startup (e.g. in the server's ``lifespan``):

    import ravi.extensions  # fires every @register_* decorator

Each subpackage's ``__init__`` re-exports its public surface and imports
its concrete classes so their decorators register on import.
"""

from __future__ import annotations

# The subpackages below trigger plugin registration via @register_*
# decorators in each module's class definitions.  Importing this package
# guarantees a single, predictable entry point.

from ravi.extensions import (  # noqa: F401
    agents,
    batch,
    context,
    extraction,
    guardrails,
    llm,
    memory,
    middleware,
    pipelines,
    rag,
    resilience,
    storage,
    structured,
    tools,
)
