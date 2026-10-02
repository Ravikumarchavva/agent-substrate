"""A ``ThreadStore`` of our own: the engine's folder store, with an audit line for every message written.

Nothing is registered anywhere. ``ThreadStore`` is a ``Protocol``: any object with those methods is one, and the agent
takes the instance. Everything we do not care about is delegated to the store underneath.
"""

from __future__ import annotations

from typing import Any

from substrate.stores import MessageNode, ThreadStore


class AuditedThreads:
    def __init__(self, inner: ThreadStore, audit: list[str]) -> None:
        self._inner = inner
        self.audit = audit

    async def append_node(self, node: MessageNode) -> None:
        self.audit.append(f"append {node.session_id}/{node.id}")
        await self._inner.append_node(node)

    async def append_and_advance(self, node: MessageNode, branch_id: str = "main", **kwargs: Any):
        self.audit.append(f"append+advance {node.session_id}/{branch_id}/{node.id}")
        return await self._inner.append_and_advance(node, branch_id, **kwargs)

    def __getattr__(self, name: str) -> Any:  # everything else: the store underneath
        return getattr(self._inner, name)
