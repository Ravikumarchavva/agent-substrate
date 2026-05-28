"""Tests for Metadata / Index Plane kernel contracts."""

from __future__ import annotations

from datetime import datetime, timezone

from ravi.kernel.metadata import MetadataRecord, Tier, compute_etag


def test_compute_etag_is_deterministic_for_equivalent_dicts() -> None:
    left = {"b": 2, "a": {"z": 1, "y": [3, 2, 1]}}
    right = {"a": {"y": [3, 2, 1], "z": 1}, "b": 2}

    assert compute_etag(left) == compute_etag(right)


def test_compute_etag_changes_when_content_changes() -> None:
    assert compute_etag({"a": 1}) != compute_etag({"a": 2})


def test_metadata_record_defaults_to_default_tenant_and_blank_etag() -> None:
    now = datetime.now(timezone.utc)
    record = MetadataRecord(
        key="k",
        value={"v": 1},
        tier=Tier.HOT,
        created_at=now,
        updated_at=now,
        accessed_at=now,
    )

    assert record.tenant_id == "default"
    assert record.etag == ""
