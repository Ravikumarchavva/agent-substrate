"""Artifacts — curated, typed knowledge kept as OKF bundles in object storage.

Two scopes: session (per-conversation) and global (per-user). Memory is one
concept ``type`` inside a bundle rather than a separate subsystem, so new
kinds of artifact need no new storage.

See ``okf.py`` for the format and ``store.py`` for the storage layout.
"""

from __future__ import annotations

from substrate.capabilities.artifacts.okf import (
    Concept,
    OKFParseError,
    agent_actor,
    human_actor,
    parse,
    serialize,
    utc_now_iso,
)
from substrate.capabilities.artifacts.store import ArtifactRef, ArtifactStore, slugify

__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "Concept",
    "OKFParseError",
    "agent_actor",
    "human_actor",
    "parse",
    "serialize",
    "slugify",
    "utc_now_iso",
]
