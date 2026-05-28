"""Tests for EnvVarRegionRegistry.

Pure unit tests — no mocking or external services needed.

Coverage
--------
- Default single-region list when no env var set
- Parsing regions from RAVI_REGIONS env var (JSON array)
- local_region_id param overrides is_local
- list_regions returns all regions
- get_region returns correct spec
- get_region raises KeyError for unknown region
- local_region returns the local region
- local_region raises RuntimeError when no local region
- mark_unavailable / mark_available toggle availability
"""

from __future__ import annotations

import json

import pytest

from ravi.integrations.control_plane import EnvVarRegionRegistry
from ravi.kernel.control_plane._contracts import RegionRegistry, RegionSpec


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    async def test_env_registry_is_region_registry_protocol(self) -> None:
        registry = EnvVarRegionRegistry()
        assert isinstance(registry, RegionRegistry)


# ===========================================================================
# Default behaviour
# ===========================================================================


class TestDefaultBehaviour:
    async def test_default_single_region_when_no_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RAVI_REGIONS", raising=False)
        registry = EnvVarRegionRegistry()
        regions = await registry.list_regions()
        assert len(regions) == 1
        assert regions[0].region_id == "default"
        assert regions[0].is_local is True

    async def test_default_region_is_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RAVI_REGIONS", raising=False)
        registry = EnvVarRegionRegistry()
        region = await registry.local_region()
        assert region.available is True


# ===========================================================================
# Environment variable parsing
# ===========================================================================


class TestEnvVarParsing:
    async def test_regions_parsed_from_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.dumps(
            [
                {"region_id": "us-east-1", "latency_ms": 5.0, "is_local": True},
                {"region_id": "eu-west-1", "latency_ms": 80.0, "is_local": False},
            ]
        )
        monkeypatch.setenv("RAVI_REGIONS", payload)
        registry = EnvVarRegionRegistry()
        regions = await registry.list_regions()
        region_ids = {r.region_id for r in regions}
        assert "us-east-1" in region_ids
        assert "eu-west-1" in region_ids

    async def test_env_var_latency_parsed_correctly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.dumps(
            [{"region_id": "ap-south-1", "latency_ms": 150.5, "is_local": True}]
        )
        monkeypatch.setenv("RAVI_REGIONS", payload)
        registry = EnvVarRegionRegistry()
        region = await registry.get_region("ap-south-1")
        assert region.latency_ms == 150.5

    async def test_explicit_regions_override_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAVI_REGIONS", json.dumps([{"region_id": "env-region", "latency_ms": 10.0}]))
        explicit = [RegionSpec(region_id="explicit-region", latency_ms=0.0, is_local=True)]
        registry = EnvVarRegionRegistry(regions=explicit)
        regions = await registry.list_regions()
        region_ids = {r.region_id for r in regions}
        assert "explicit-region" in region_ids
        assert "env-region" not in region_ids


# ===========================================================================
# local_region_id parameter
# ===========================================================================


class TestLocalRegionId:
    async def test_local_region_id_overrides_is_local(self) -> None:
        regions = [
            RegionSpec(region_id="r1", latency_ms=0.0, is_local=True),
            RegionSpec(region_id="r2", latency_ms=10.0, is_local=False),
        ]
        registry = EnvVarRegionRegistry(regions=regions, local_region_id="r2")
        local = await registry.local_region()
        assert local.region_id == "r2"

    async def test_local_region_id_unmarks_previous_local(self) -> None:
        regions = [
            RegionSpec(region_id="r1", latency_ms=0.0, is_local=True),
            RegionSpec(region_id="r2", latency_ms=10.0, is_local=False),
        ]
        registry = EnvVarRegionRegistry(regions=regions, local_region_id="r2")
        r1 = await registry.get_region("r1")
        assert r1.is_local is False


# ===========================================================================
# get_region
# ===========================================================================


class TestGetRegion:
    async def test_get_region_returns_correct_spec(self) -> None:
        regions = [RegionSpec(region_id="prod-us", latency_ms=5.0, is_local=True)]
        registry = EnvVarRegionRegistry(regions=regions)
        region = await registry.get_region("prod-us")
        assert region.region_id == "prod-us"
        assert region.latency_ms == 5.0

    async def test_get_region_raises_key_error_for_unknown(self) -> None:
        registry = EnvVarRegionRegistry()
        with pytest.raises(KeyError):
            await registry.get_region("nonexistent-region")


# ===========================================================================
# local_region
# ===========================================================================


class TestLocalRegion:
    async def test_local_region_raises_when_none_marked_local(self) -> None:
        regions = [
            RegionSpec(region_id="r1", latency_ms=0.0, is_local=False),
            RegionSpec(region_id="r2", latency_ms=10.0, is_local=False),
        ]
        registry = EnvVarRegionRegistry(regions=regions)
        with pytest.raises(RuntimeError, match="no local region configured"):
            await registry.local_region()

    async def test_local_region_returns_first_local(self) -> None:
        regions = [RegionSpec(region_id="local-r", latency_ms=0.0, is_local=True)]
        registry = EnvVarRegionRegistry(regions=regions)
        local = await registry.local_region()
        assert local.region_id == "local-r"


# ===========================================================================
# mark_unavailable / mark_available
# ===========================================================================


class TestAvailabilityToggle:
    async def test_mark_unavailable_sets_available_false(self) -> None:
        regions = [RegionSpec(region_id="r1", latency_ms=0.0, is_local=True, available=True)]
        registry = EnvVarRegionRegistry(regions=regions)
        await registry.mark_unavailable("r1")
        r1 = await registry.get_region("r1")
        assert r1.available is False

    async def test_mark_available_sets_available_true(self) -> None:
        regions = [RegionSpec(region_id="r1", latency_ms=0.0, is_local=True, available=False)]
        registry = EnvVarRegionRegistry(regions=regions)
        await registry.mark_available("r1")
        r1 = await registry.get_region("r1")
        assert r1.available is True

    async def test_mark_unavailable_unknown_region_is_noop(self) -> None:
        registry = EnvVarRegionRegistry()
        # Should not raise
        await registry.mark_unavailable("nonexistent")

    async def test_mark_available_unknown_region_is_noop(self) -> None:
        registry = EnvVarRegionRegistry()
        # Should not raise
        await registry.mark_available("nonexistent")
