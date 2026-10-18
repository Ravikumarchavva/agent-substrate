"""A read handle on the runtime's durable store, for the services that read runs but do not execute them.

The run journal is the one source of truth for a conversation (``stream/history.py``): the conversation service projects messages
from it, the live-stream service tails it and the human gate signals through it. Opening it starts no worker.
"""

from __future__ import annotations

from typing import Any

from substrate.runtime import Runtime

from substrate_cloud.factory import open_store
from substrate_cloud.shared.settings import settings


async def open_runtime_view(store: Any = None) -> tuple[Any, Runtime]:
    """``(store, runtime)``: the platform's store (opened from settings unless one is given) and an idle runtime over it."""
    store = store if store is not None else open_store(settings)
    await store.start()
    runtime = Runtime(store)
    await runtime.store.start()
    return store, runtime
