"""``DataMemoryStore``: the ``MemoryStore`` contract over substrate-data's Memory domain (ADR 0004).

Long-term memory is user content, so it lives behind the data service's grants, tenancy and audit. The service takes the tenant from
the token and lets a token speak only as its own user; this adapter names the namespace's owner fields and asks the platform's
``tokens(tenant, user)`` for the matching delegated token (``memory:write`` covers reads).

Memory is context, not a dependency: reads are **fail-soft**. A short cache (60 seconds by default) answers repeated lookups of the same
turn, and when the service is unreachable or busy a read returns what the cache last had — or nothing — rather than failing the run.
Writes are not softened: a fact the agent was told was saved must have been.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from substrate_data_client import (
    DataError,
    Forbidden,
    InsufficientScope,
    NotFound,
    RateLimited,
    Unavailable,
)

from substrate.integrations.data._connection import (
    DataServiceConnection,
    DocumentTokens,
    mint,
)
from substrate.integrations.data._errors import raise_for
from substrate.stores.memory import (
    MemoryMatch,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)
from substrate.types.errors import AuthError, ScopeViolationError

logger = logging.getLogger(__name__)

MemoryTokens = DocumentTokens
"""``(tenant_id, user_id or None) -> bearer`` carrying ``memory:write``. With no user the token must be a tenant administrator's: the
platform's own jobs act that way."""

_MAX_ENTRIES = 1024


def _owner(namespace: MemoryNamespace) -> dict[str, str]:
    return {
        k: v
        for k, v in (
            ("user_id", namespace.user_id),
            ("agent_id", namespace.agent_id),
            ("session_id", namespace.session_id),
        )
        if v is not None
    }


def _soft(exc: DataError) -> bool:
    return isinstance(exc, (Unavailable, RateLimited))


class DataMemoryStore:
    def __init__(
        self,
        connection: DataServiceConnection,
        tokens: MemoryTokens,
        *,
        cache_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connection = connection
        self._tokens = tokens
        self._ttl = cache_seconds
        self._clock = clock
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, Any]] = OrderedDict()

    # ------------------------------------------------------------------ plumbing
    async def _call(
        self, namespace: MemoryNamespace, method: str, *args: Any, **kwargs: Any
    ) -> Any:
        token = await mint(self._tokens, namespace.tenant_id, namespace.user_id)
        with self._connection.bearer(token):
            return await getattr(self._connection.client.memory, method)(
                *args, **kwargs
            )

    def _remember(self, key: tuple[Any, ...], value: Any) -> None:
        if self._ttl <= 0:
            return
        self._cache[key] = (self._clock(), value)
        self._cache.move_to_end(key)
        while len(self._cache) > _MAX_ENTRIES:
            self._cache.popitem(last=False)

    def _recall(self, key: tuple[Any, ...], *, stale: bool = False) -> tuple[bool, Any]:
        found = self._cache.get(key)
        if found is None:
            return False, None
        at, value = found
        if stale or self._clock() - at < self._ttl:
            return True, value
        return False, None

    def _forget(self, tenant: str) -> None:
        """A write by this process: drop what it cached for the tenant (other replicas' writes show up within the TTL)."""
        for key in [k for k in self._cache if k[1] == tenant]:
            del self._cache[key]

    @staticmethod
    def _ns_key(namespace: MemoryNamespace) -> tuple[Any, ...]:
        return (
            namespace.tenant_id,
            namespace.user_id,
            namespace.agent_id,
            namespace.session_id,
        )

    # ------------------------------------------------------------------ MemoryStore
    async def save(self, record: MemoryRecord) -> str:
        namespace = record.namespace
        try:
            await self._call(namespace, "save", json.loads(record.model_dump_json()))
        except Forbidden as exc:
            if isinstance(exc, InsufficientScope):
                raise_for(exc, record.id)
            raise ScopeViolationError(
                f"not allowed to save memory record {record.id!r}: {exc.detail}",
                record_id=record.id,
            ) from exc
        except DataError as exc:
            raise_for(exc, record.id)
        finally:
            self._forget(namespace.tenant_id)
        return record.id

    async def get(self, caller: MemoryNamespace, record_id: str) -> MemoryRecord | None:
        key = ("get", caller.tenant_id, *self._ns_key(caller)[1:], record_id)
        hit, value = self._recall(key)
        if hit:
            return value
        try:
            found = await self._call(caller, "get", record_id, **_owner(caller))
        except AuthError as exc:
            logger.warning("memory read skipped: %s", exc)  # context, not a dependency
            return None
        except DataError as exc:
            if isinstance(exc, (NotFound, Forbidden)) and not isinstance(
                exc, InsufficientScope
            ):
                return None
            if _soft(exc):
                logger.warning(
                    "memory is unavailable (%s); answering from the cache", exc.code
                )
                return self._recall(key, stale=True)[1]
            raise_for(exc, record_id)
        record = MemoryRecord.model_validate(found)
        self._remember(key, record)
        return record

    async def delete(self, caller: MemoryNamespace, record_id: str) -> bool:
        try:
            await self._call(caller, "delete", record_id, **_owner(caller))
        except DataError as exc:
            if isinstance(exc, (NotFound, Forbidden)) and not isinstance(
                exc, InsufficientScope
            ):
                return False
            raise_for(exc, record_id)
        finally:
            self._forget(caller.tenant_id)
        return True

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        body = json.loads(spec.model_dump_json(exclude={"namespace", "tenant_wide"}))
        key = (
            "query",
            *self._ns_key(spec.namespace),
            json.dumps(
                [body, spec.tenant_wide.reason if spec.tenant_wide else None],
                sort_keys=True,
            ),
        )
        hit, value = self._recall(key)
        if hit:
            return list(value)
        if spec.tenant_wide is not None:
            body["tenant_wide_reason"] = spec.tenant_wide.reason
        try:
            found = await self._call(
                spec.namespace, "query", **_owner(spec.namespace), **body
            )
        except AuthError as exc:
            logger.warning("memory read skipped: %s", exc)
            return []
        except DataError as exc:
            if _soft(exc):
                logger.warning(
                    "memory is unavailable (%s); answering from the cache", exc.code
                )
                return list(self._recall(key, stale=True)[1] or [])
            if isinstance(exc, (NotFound, Forbidden)) and not isinstance(
                exc, InsufficientScope
            ):
                return []
            raise_for(exc, "memory")
        matches = [MemoryMatch.model_validate(m) for m in found]
        self._remember(key, matches)
        return list(matches)

    async def touch(self, caller: MemoryNamespace, record_ids: Sequence[str]) -> None:
        if not record_ids:
            return
        try:
            await self._call(caller, "touch", list(record_ids), **_owner(caller))
        except DataError as exc:
            logger.warning(
                "memory touch skipped (%s)", exc.code
            )  # an access count is not worth failing a turn for
        finally:
            self._forget(caller.tenant_id)

    async def erase(self, within: MemoryNamespace) -> int:
        try:
            return int(await self._call(within, "erase", **_owner(within)))
        except DataError as exc:
            raise_for(exc, "memory")
        finally:
            self._forget(within.tenant_id)


__all__ = ["DataMemoryStore", "MemoryTokens"]
