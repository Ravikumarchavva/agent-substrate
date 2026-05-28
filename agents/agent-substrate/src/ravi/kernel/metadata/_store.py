"""Metadata / Index Plane — tiered key/value records with prefix scan.

The Metadata Plane is the kernel's small-object index — workflow descriptors,
trigger configurations, principal hints, tool versions, anything that's
looked up by string key and benefits from a two-tier (hot/cold) layout where
"hot" entries stay in low-latency storage and "cold" entries are flushed to
bulk/archive backends.

The contract is intentionally minimal: ``put``/``get``/``delete``/``scan``
plus explicit tier transitions (``promote``/``demote``) and a sweep operation
(``compact``) that auto-demotes stale hot entries. Real production backends
will plug Redis (hot) + Postgres or S3 (cold) into the same Protocol.

Thread-safety
~~~~~~~~~~~~~
Implementations live above the kernel layer (typically ``extensions/metadata/``)
and are accessed from the agent loop, scheduled compaction threads, and
operator tooling. They must therefore be free-threading safe — see
:class:`InMemoryLeaseRegistry` for the canonical ``threading.RLock`` guard
template.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Tier",
    "MetadataRecord",
    "KeyNotFoundError",
    "MetadataStore",
    "compute_etag",
]


class Tier(Enum):
    """Storage tier for a :class:`MetadataRecord`.

    - ``HOT``  — low-latency, frequently-accessed entries (Redis-class backends).
    - ``COLD`` — bulk / archive entries (Postgres / S3-class backends).
    """

    HOT = "hot"
    COLD = "cold"


def compute_etag(value: dict[str, Any]) -> str:
    """Deterministic content-addressed etag for a record's value.

    Uses canonical JSON (sorted keys, no extra whitespace) so two equal
    dicts always yield the same etag regardless of insertion order.
    """

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MetadataRecord:
    """A single metadata entry — value plus tier and audit timestamps.

    ``etag`` is a sha256 hex of the canonical-JSON-encoded ``value`` and is
    recomputed on every ``put``. Callers can use it as an HTTP ETag or for
    optimistic-concurrency checks without rehashing.
    """

    key: str
    value: dict[str, Any]
    tier: Tier
    created_at: datetime
    updated_at: datetime
    accessed_at: datetime
    tenant_id: str = "default"
    etag: str = ""


class KeyNotFoundError(KeyError):
    """Raised by :meth:`MetadataStore.get` when the key is absent."""


@runtime_checkable
class MetadataStore(Protocol):
    """Tiered metadata index — small objects looked up by string key.

    Implementations must serialise concurrent ``put``/``get``/``delete``
    calls so the per-key state is consistent under any interleaving,
    including no-GIL free-threaded interleavings.
    """

    async def put(
        self,
        key: str,
        value: dict[str, Any],
        *,
        tier: Tier = Tier.HOT,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Insert or update ``key`` for ``tenant_id``.

        If the key already exists (in either tier), the record is updated
        *in place* at its current tier — ``created_at`` is preserved,
        ``updated_at`` is bumped, and ``etag`` is recomputed.
        Otherwise a new record is inserted into the requested ``tier``.
        """
        ...

    async def get(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord:
        """Return the record for ``key``; raise :class:`KeyNotFoundError`
        when absent.
        """
        ...

    async def get_or_none(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord | None:
        """Return the record for ``key`` or ``None`` when absent."""
        ...

    async def delete(self, key: str, *, tenant_id: str = "default") -> bool:
        """Remove ``key``. Return ``True`` when something was deleted,
        ``False`` when the key was already absent.
        """
        ...

    async def scan_prefix(
        self,
        prefix: str,
        *,
        tenant_id: str = "default",
        limit: int = 100,
    ) -> list[MetadataRecord]:
        """Return records whose key starts with ``prefix``, in lexicographic
        order, capped at ``limit``.
        """
        ...

    async def promote(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord:
        """Move ``key`` to :attr:`Tier.HOT`. No-op if already hot."""
        ...

    async def demote(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord:
        """Move ``key`` to :attr:`Tier.COLD`. No-op if already cold."""
        ...

    async def compact(self, *, tenant_id: str = "default") -> int:
        """Sweep tenant metadata, applying tier-maintenance rules.

        Returns the number of entries acted on (e.g. auto-demoted from
        hot to cold for being idle past their TTL).
        """
        ...
