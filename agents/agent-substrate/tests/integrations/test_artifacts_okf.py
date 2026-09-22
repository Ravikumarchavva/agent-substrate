"""OKF v0.2 conformance for the artifact format.

The spec's two load-bearing rules for a *consumer* are that ``type`` is the
only required field and that unknown/missing optional fields must never
cause rejection — these tests pin both, since a stricter parser would
silently drop third-party bundles.
"""

from __future__ import annotations

import pytest

from substrate.capabilities.artifacts.okf import (
    Concept,
    OKFParseError,
    human_actor,
    parse,
    serialize,
)
from substrate.capabilities.artifacts.store import slugify


def test_type_is_the_only_required_field():
    doc = "---\ntype: Memory\n---\n\nUser prefers terse answers.\n"
    concept = parse(doc)
    assert concept.type == "Memory"
    assert concept.body == "User prefers terse answers."
    assert concept.status == "stable"  # spec default


def test_missing_type_is_rejected():
    with pytest.raises(OKFParseError):
        parse("---\ntitle: No type here\n---\n\nbody\n")


def test_missing_frontmatter_is_rejected():
    with pytest.raises(OKFParseError):
        parse("just markdown, no frontmatter\n")


def test_unknown_keys_are_preserved_not_dropped():
    """Spec: consumers MUST NOT reject unknown additional frontmatter keys.
    Preserving them matters because a round-trip through our editor must not
    destroy fields written by another OKF producer."""
    doc = "---\ntype: Memory\nvendor_specific: keep-me\n---\n\nbody\n"
    concept = parse(doc)
    assert concept.extra["vendor_specific"] == "keep-me"
    assert "vendor_specific: keep-me" in serialize(concept)


def test_single_verified_entry_may_omit_list_brackets():
    """The spec allows a lone verification event to be a bare mapping."""
    doc = "---\ntype: Memory\nverified: { by: 'human:ravi', at: 2026-09-13T10:00:00Z }\n---\n\nb\n"
    concept = parse(doc)
    assert len(concept.verified) == 1
    assert concept.trust_tier == "Human-reviewed"


def test_trust_tiers_derive_from_verified():
    unverified = Concept(type="Memory")
    assert unverified.trust_tier == "Unverified"

    machine = Concept(type="Memory", verified=[{"by": "substrate/gpt-5.4-mini"}])
    assert machine.trust_tier == "Machine-confirmed"

    human = Concept(
        type="Memory",
        verified=[{"by": "substrate/gpt-5.4-mini"}, {"by": human_actor("ravi")}],
    )
    assert human.trust_tier == "Human-reviewed"


def test_unknown_status_value_falls_back_instead_of_failing():
    """An unrecognized *value* is not grounds for rejection."""
    concept = parse("---\ntype: Memory\nstatus: bogus\n---\n\nb\n")
    assert concept.status == "stable"


def test_deprecate_invalidates_without_deleting_content():
    """Zep-style: a superseded fact keeps its body and gains a timestamp,
    so history stays readable rather than being overwritten."""
    concept = parse("---\ntype: Memory\n---\n\nLives in Hyderabad.\n")
    concept.deprecate()
    assert concept.status == "deprecated"
    assert concept.stale_after is not None
    assert concept.is_stale()
    assert "Hyderabad" in concept.body


def test_unparseable_stale_after_is_not_treated_as_stale():
    """A malformed optional field must not hide a concept."""
    concept = parse("---\ntype: Memory\nstale_after: not-a-date\n---\n\nb\n")
    assert concept.is_stale() is False


def test_mark_verified_by_is_idempotent_per_actor():
    concept = Concept(type="Memory")
    concept.mark_verified_by(human_actor("ravi"), at="2026-09-13T10:00:00Z")
    concept.mark_verified_by(human_actor("ravi"), at="2026-09-13T11:00:00Z")
    assert len(concept.verified) == 1
    assert concept.verified[0]["at"] == "2026-09-13T11:00:00Z"


def test_round_trip_preserves_every_field():
    original = Concept(
        type="Memory",
        title="Prefers terse answers",
        description="No trailing summaries.",
        resource="tenants/t1/conversations/c1/workspace/shared/uploads/a.xlsx",
        tags=["communication", "style"],
        status="draft",
        generated={"by": "substrate/gpt-5.4-mini", "at": "2026-09-13T10:00:00Z"},
        verified=[{"by": "human:ravi", "at": "2026-09-13T10:05:00Z"}],
        body="Body text.",
    )
    reparsed = parse(serialize(original))
    assert reparsed.type == original.type
    assert reparsed.title == original.title
    assert reparsed.description == original.description
    assert reparsed.resource == original.resource
    assert reparsed.tags == original.tags
    assert reparsed.status == original.status
    assert reparsed.generated == original.generated
    assert reparsed.verified == original.verified
    assert reparsed.body == original.body


def test_default_status_is_not_written_to_the_file():
    """Keeps a minimal concept minimal — 'stable' is the spec default."""
    assert "status:" not in serialize(Concept(type="Memory", body="b"))


def test_slugify_is_object_key_safe():
    assert slugify("Prefers Terse Answers!") == "prefers-terse-answers"
    assert slugify("café / naïve") == "cafe-naive"
    assert slugify("!!!") == "note"  # fallback when nothing survives
