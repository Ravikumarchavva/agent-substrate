"""Activity context — provides singletons to Temporal activities.

Activities run in a worker process and need access to the CapabilityRegistry,
DataRefStore and ChainRuntime. This module stores them as module-level globals
that are set once during worker startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from raavan.catalog._chain_runtime import ChainRuntime
    from raavan.catalog._data_ref import DataRefStore
    from raavan.core.tools.catalog import CapabilityRegistry

_catalog: CapabilityRegistry | None = None
_data_store: DataRefStore | None = None
_chain_runtime: ChainRuntime | None = None


def init_activity_context(
    catalog: CapabilityRegistry,
    data_store: DataRefStore | None = None,
    chain_runtime: ChainRuntime | None = None,
) -> None:
    """Called once at worker startup to inject shared state."""
    global _catalog, _data_store, _chain_runtime
    _catalog = catalog
    _data_store = data_store
    _chain_runtime = chain_runtime


def get_catalog() -> CapabilityRegistry:
    if _catalog is None:
        raise RuntimeError(
            "Activity context not initialised — call init_activity_context()"
        )
    return _catalog


def get_data_store() -> DataRefStore | None:
    return _data_store


def get_chain_runtime() -> ChainRuntime | None:
    return _chain_runtime
