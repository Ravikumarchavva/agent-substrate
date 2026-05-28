"""Metadata / Index Plane contracts (Section 8).

Tiered key/value index for small objects looked up by string key. See
:mod:`ravi.kernel.metadata._store` for the contract; reference
implementations live in :mod:`ravi.fabric.metadata`.
"""

from __future__ import annotations

from ravi.kernel.metadata._store import (
    KeyNotFoundError,
    MetadataRecord,
    MetadataStore,
    Tier,
    compute_etag,
)

__all__ = [
    "KeyNotFoundError",
    "MetadataRecord",
    "MetadataStore",
    "Tier",
    "compute_etag",
]
