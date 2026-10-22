"""`DataMemoryStore`: the `MemoryStore` contract over Loamz Data's Memory domain.

Long-term memory is user content, so it lives behind the data service's grants, tenancy and audit.
"""

from __future__ import annotations

from collections.abc import Sequence

from loamz.core.exceptions import NotFoundError as ClientNotFoundError
from loamz.engine.agents.data._connection import DataServiceConnection, TokenProvider, mint
from loamz.engine.memory import (
    MemoryMatch,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
    MemoryStore,
)


class DataMemoryStore(MemoryStore):
    """Memory store backed by the Loamz Data memory service."""

    def __init__(self, connection: DataServiceConnection, tokens: TokenProvider) -> None:
        self._connection = connection
        self._tokens = tokens

    async def save(self, record: MemoryRecord) -> str:
        token = await mint(self._tokens, record.namespace.tenant_id, record.namespace.user_id)
        with self._connection.bearer(token):
            await self._connection.client.memory.save(record.model_dump(mode="json"))
        return record.id

    async def get(self, caller: MemoryNamespace, record_id: str) -> MemoryRecord | None:
        token = await mint(self._tokens, caller.tenant_id, caller.user_id)
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.memory.get(
                    record_id,
                    user_id=caller.user_id,
                    agent_id=caller.agent_id,
                    session_id=caller.session_id,
                )
                if not raw:
                    return None
                return MemoryRecord.model_validate(raw)
            except ClientNotFoundError:
                return None

    async def delete(self, caller: MemoryNamespace, record_id: str) -> bool:
        token = await mint(self._tokens, caller.tenant_id, caller.user_id)
        with self._connection.bearer(token):
            try:
                await self._connection.client.memory.delete(
                    record_id,
                    user_id=caller.user_id,
                    agent_id=caller.agent_id,
                    session_id=caller.session_id,
                )
                return True
            except ClientNotFoundError:
                return False

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        token = await mint(self._tokens, spec.namespace.tenant_id, spec.namespace.user_id)
        with self._connection.bearer(token):
            results = await self._connection.client.memory.query(
                user_id=spec.namespace.user_id,
                agent_id=spec.namespace.agent_id,
                session_id=spec.namespace.session_id,
                tenant_wide_reason=spec.tenant_wide.reason if spec.tenant_wide else None,
                text_query=spec.text_query,
                embedding=spec.embedding,
                categories=[c.value for c in spec.categories] if spec.categories else None,
                statuses=[s.value for s in spec.statuses] if spec.statuses else None,
                limit=spec.limit,
                min_score=spec.min_score,
                metadata_filter=spec.metadata_filter,
            )
            return [MemoryMatch.model_validate(m) for m in results]

    async def touch(self, caller: MemoryNamespace, record_ids: Sequence[str]) -> None:
        token = await mint(self._tokens, caller.tenant_id, caller.user_id)
        with self._connection.bearer(token):
            await self._connection.client.memory.touch(
                list(record_ids),
                user_id=caller.user_id,
                agent_id=caller.agent_id,
                session_id=caller.session_id,
            )

    async def erase(self, within: MemoryNamespace) -> int:
        token = await mint(self._tokens, within.tenant_id, within.user_id)
        with self._connection.bearer(token):
            res = await self._connection.client.memory.erase(
                user_id=within.user_id,
                agent_id=within.agent_id,
                session_id=within.session_id,
            )
            return int(res)
