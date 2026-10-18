"""`client.memory`: long-term memory records (substrate-data's Memory domain).

    client.memory.save("rec-1", {"content": [{"type": "text", "text": "likes tea"}], "namespace": {"user_id": "u1"}})
    client.memory.query(user_id="u1", text_query="tea")

Records are the agent library's `MemoryRecord` as JSON; `substrate.integrations.data.DataMemoryStore` converts. The tenant is
always the token's. The methods are written once: on `AsyncDataClient` the same calls are awaited.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from substrate_data_client._http import AsyncCore, SyncCore


def _owner(user_id: str | None, agent_id: str | None, session_id: str | None) -> dict[str, str]:
    return {
        k: v
        for k, v in (("user_id", user_id), ("agent_id", agent_id), ("session_id", session_id))
        if v is not None
    }


class _MemoryCalls:
    def _do(self, method: str, path: str, post: Callable | None = None, **kwargs: Any):
        raise NotImplementedError

    def save(self, record_id: str, record: dict[str, Any]):
        """Upsert by id within the tenant. 403 `scope-violation` when the id belongs to another owner."""
        return self._do("PUT", f"/memory/records/{quote(record_id, safe='')}", json=record)

    def get(
        self, record_id: str, *, user_id: str | None = None, agent_id: str | None = None, session_id: str | None = None
    ):
        return self._do(
            "GET",
            f"/memory/records/{quote(record_id, safe='')}",
            params=_owner(user_id, agent_id, session_id),
        )

    def delete(
        self, record_id: str, *, user_id: str | None = None, agent_id: str | None = None, session_id: str | None = None
    ):
        return self._do(
            "DELETE",
            f"/memory/records/{quote(record_id, safe='')}",
            params=_owner(user_id, agent_id, session_id),
        )

    def query(
        self,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        tenant_wide_reason: str | None = None,
        **spec: Any,
    ):
        """Matches visible from the namespace: `text_query`, `embedding`, `categories`, `statuses`, `limit`, `min_score`,
        `metadata_filter`. Returns the list of `{record, score, rank, retrieval_method}`."""
        body = {"namespace": _owner(user_id, agent_id, session_id), **spec}
        if tenant_wide_reason:
            body["tenant_wide_reason"] = tenant_wide_reason
        return self._do("POST", "/memory/records:query", lambda b: b["matches"], json=body)

    def touch(
        self,
        ids: list[str],
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ):
        return self._do(
            "POST",
            "/memory/records:touch",
            json={"namespace": _owner(user_id, agent_id, session_id), "ids": ids},
        )


class Memory(_MemoryCalls):
    def __init__(self, core: SyncCore):
        self._core = core

    def _do(self, method, path, post=None, **kwargs):
        body = self._core.call(method, path, **kwargs)
        return post(body) if post else body


class AsyncMemory(_MemoryCalls):
    def __init__(self, core: AsyncCore):
        self._core = core

    async def _do(self, method, path, post=None, **kwargs):
        body = await self._core.call(method, path, **kwargs)
        return post(body) if post else body
