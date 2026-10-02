"""OKF concept parsing/serialization.

Implements Google Cloud's Open Knowledge Format v0.2
(https://github.com/GoogleCloudPlatform/open-knowledge-format): a bundle is a
directory of markdown files with YAML frontmatter, where ``type`` is the only
required field, plus the reserved filenames ``index.md`` and ``log.md``.

Two spec rules shape this module:

* **Permissive consumption.** A consumer "MUST NOT reject a bundle because
  of: missing optional frontmatter fields, unknown type values, unknown
  additional frontmatter keys, broken cross-links, or missing index.md".
  So ``parse`` keeps unrecognized keys in ``extra`` and round-trips them
  instead of dropping them, and only a missing/empty ``type`` makes a
  document non-conforming.
* **Lifecycle over deletion.** ``status`` (draft/stable/deprecated) and
  ``stale_after`` exist so a superseded fact can be invalidated in place
  rather than overwritten — the behavior Zep's temporal graph is built
  around ("I moved to Denver" invalidates the old city, keeping history).
  ``deprecate()`` is therefore the default way to retire a concept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml

# Reserved by the spec — never concept documents.
INDEX_FILENAME = "index.md"
LOG_FILENAME = "log.md"
RESERVED_FILENAMES = frozenset({INDEX_FILENAME, LOG_FILENAME})

_FRONTMATTER_FENCE = "---"

# Frontmatter keys this module maps onto typed fields; everything else is
# preserved verbatim in `extra` (spec: unknown keys must not be rejected).
_KNOWN_KEYS = frozenset(
    {
        "type",
        "title",
        "description",
        "resource",
        "tags",
        "status",
        "stale_after",
        "generated",
        "verified",
        "sources",
    }
)

VALID_STATUSES = frozenset({"draft", "stable", "deprecated"})


def utc_now_iso() -> str:
    """ISO-8601 in UTC with a trailing ``Z`` — the form the spec's examples use."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_actor(user_id: str) -> str:
    """``human:<id>`` — the spec's actor form for a person.

    Drives the trust tier: any ``verified`` entry by a ``human:`` actor makes
    a concept Human-reviewed rather than merely Machine-confirmed.
    """
    return f"human:{user_id}"


def agent_actor(producer: str, version: str) -> str:
    """``<producer>/<version>`` — the spec's actor form for an agent."""
    return f"{producer}/{version}"


class OKFParseError(ValueError):
    """Raised only for genuinely non-conforming input (no parseable
    frontmatter, or no non-empty ``type``) — never for unknown keys."""


@dataclass
class Concept:
    """One OKF concept document.

    ``type`` is the sole required field. ``body`` is the markdown after the
    frontmatter block.
    """

    type: str
    body: str = ""
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] = field(default_factory=list)
    status: str = "stable"
    stale_after: str | None = None
    generated: dict[str, Any] | None = None
    verified: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def trust_tier(self) -> str:
        """Spec §trust tiers, derived purely from ``verified``.

        No verification → Unverified; verified only by non-human actors →
        Machine-confirmed; verified by any ``human:<id>`` → Human-reviewed.
        """
        if not self.verified:
            return "Unverified"
        for entry in self.verified:
            by = str(entry.get("by", ""))
            if by.startswith("human:"):
                return "Human-reviewed"
        return "Machine-confirmed"

    def is_stale(self, *, now: datetime | None = None) -> bool:
        """True once ``stale_after`` has passed. Unparseable values are
        treated as not-stale: the spec forbids rejecting a bundle over a
        malformed optional field, so a bad timestamp must not hide content."""
        if not self.stale_after:
            return False
        raw = self.stale_after.replace("Z", "+00:00")
        try:
            deadline = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) >= deadline

    def mark_verified_by(self, actor: str, *, at: str | None = None) -> None:
        """Append a verification event, de-duplicated per actor so repeated
        confirmations don't inflate the list."""
        stamp = at or utc_now_iso()
        for entry in self.verified:
            if entry.get("by") == actor:
                entry["at"] = stamp
                return
        self.verified.append({"by": actor, "at": stamp})

    def deprecate(self, *, at: str | None = None) -> None:
        """Retire without deleting — see module docstring."""
        self.status = "deprecated"
        self.stale_after = at or utc_now_iso()


def parse(text: str) -> Concept:
    """Parse an OKF concept document.

    Raises ``OKFParseError`` only when the document cannot conform: no
    frontmatter block, frontmatter that isn't a YAML mapping, or a
    missing/empty ``type``.
    """
    stripped = text.lstrip("﻿")
    if not stripped.startswith(_FRONTMATTER_FENCE):
        raise OKFParseError("missing YAML frontmatter block")

    # Split on the closing fence: everything up to it is YAML, the rest is body.
    rest = stripped[len(_FRONTMATTER_FENCE) :].lstrip("\r\n")
    end = rest.find(f"\n{_FRONTMATTER_FENCE}")
    if end == -1:
        raise OKFParseError("unterminated YAML frontmatter block")
    raw_yaml = rest[:end]
    # Normalized the same way `serialize` writes it, so parse(serialize(c))
    # is idempotent rather than accreting a newline per round-trip.
    body = rest[end + len(_FRONTMATTER_FENCE) + 1 :].strip()

    try:
        loaded = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as exc:
        raise OKFParseError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(loaded, dict):
        raise OKFParseError("frontmatter is not a YAML mapping")

    concept_type = loaded.get("type")
    if not isinstance(concept_type, str) or not concept_type.strip():
        raise OKFParseError("frontmatter has no non-empty 'type'")

    # The spec allows a single verification event to omit the list brackets.
    verified_raw = loaded.get("verified") or []
    if isinstance(verified_raw, dict):
        verified_raw = [verified_raw]
    tags_raw = loaded.get("tags") or []
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]

    status = loaded.get("status") or "stable"
    if status not in VALID_STATUSES:
        # Unknown status is an unknown *value*, not grounds for rejection.
        status = "stable"

    return Concept(
        type=concept_type.strip(),
        body=body,
        title=loaded.get("title"),
        description=loaded.get("description"),
        resource=loaded.get("resource"),
        tags=[str(t) for t in tags_raw],
        status=status,
        stale_after=loaded.get("stale_after"),
        generated=loaded.get("generated"),
        verified=[dict(v) for v in verified_raw if isinstance(v, dict)],
        sources=[dict(s) for s in (loaded.get("sources") or []) if isinstance(s, dict)],
        extra={k: v for k, v in loaded.items() if k not in _KNOWN_KEYS},
    )


def serialize(concept: Concept) -> str:
    """Render a concept back to an OKF document.

    Omits empty optional fields so a minimal concept stays minimal, and
    re-emits ``extra`` so unknown keys survive a read/write round-trip.
    """
    data: dict[str, Any] = {"type": concept.type}
    if concept.title:
        data["title"] = concept.title
    if concept.description:
        data["description"] = concept.description
    if concept.resource:
        data["resource"] = concept.resource
    if concept.tags:
        data["tags"] = list(concept.tags)
    # "stable" is the spec default — emitting it adds noise to every file.
    if concept.status and concept.status != "stable":
        data["status"] = concept.status
    if concept.stale_after:
        data["stale_after"] = concept.stale_after
    if concept.generated:
        data["generated"] = concept.generated
    if concept.verified:
        data["verified"] = concept.verified
    if concept.sources:
        data["sources"] = concept.sources
    data.update(concept.extra)

    front = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip("\n")
    body = concept.body.strip()
    return f"{_FRONTMATTER_FENCE}\n{front}\n{_FRONTMATTER_FENCE}\n\n{body}\n"


__all__ = [
    "INDEX_FILENAME",
    "LOG_FILENAME",
    "RESERVED_FILENAMES",
    "VALID_STATUSES",
    "Concept",
    "OKFParseError",
    "agent_actor",
    "human_actor",
    "parse",
    "serialize",
    "utc_now_iso",
]
