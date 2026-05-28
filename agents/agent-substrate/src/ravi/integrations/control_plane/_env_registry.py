"""Environment-variable-driven RegionRegistry integration.

Implements :class:`ravi.kernel.control_plane._contracts.RegionRegistry` using
a static list of :class:`RegionSpec` objects.  The list may be supplied
directly or parsed from the ``RAVI_REGIONS`` environment variable (JSON array).

Thread-safety
~~~~~~~~~~~~~
All shared state is guarded by ``threading.RLock``.  No lock is held across
``await``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from typing import Sequence

from ravi.kernel.control_plane._contracts import RegionSpec

__all__ = ["EnvVarRegionRegistry"]

_DEFAULT_REGIONS: list[RegionSpec] = [
    RegionSpec(region_id="default", latency_ms=0.0, is_local=True)
]


def _parse_regions(raw: str) -> list[RegionSpec]:
    """Parse a JSON array of region dicts into :class:`RegionSpec` objects."""
    data = json.loads(raw)
    regions: list[RegionSpec] = []
    for item in data:
        regions.append(
            RegionSpec(
                region_id=item["region_id"],
                latency_ms=float(item.get("latency_ms", 0.0)),
                weight=float(item.get("weight", 1.0)),
                is_local=bool(item.get("is_local", False)),
                available=bool(item.get("available", True)),
            )
        )
    return regions


class EnvVarRegionRegistry:
    """Env-var-driven implementation of :class:`RegionRegistry`.

    Parameters
    ----------
    regions:
        Explicit list of regions.  When ``None``, the ``RAVI_REGIONS``
        environment variable is checked; if unset the default single-region
        list is used.
    local_region_id:
        When provided, only the region with this ID will be marked
        ``is_local=True``; all others will have ``is_local=False``.
    """

    def __init__(
        self,
        regions: list[RegionSpec] | None = None,
        *,
        local_region_id: str | None = None,
    ) -> None:
        if regions is None:
            raw = os.environ.get("RAVI_REGIONS")
            if raw:
                regions = _parse_regions(raw)
            else:
                regions = list(_DEFAULT_REGIONS)

        if local_region_id is not None:
            regions = [
                dataclasses.replace(r, is_local=(r.region_id == local_region_id))
                for r in regions
            ]

        self._lock = threading.RLock()
        self._regions: dict[str, RegionSpec] = {r.region_id: r for r in regions}

    # ------------------------------------------------------------------
    # RegionRegistry protocol
    # ------------------------------------------------------------------

    async def list_regions(self) -> Sequence[RegionSpec]:
        """Return all known :class:`RegionSpec` objects."""
        with self._lock:
            return list(self._regions.values())

    async def get_region(self, region_id: str) -> RegionSpec:
        """Return the :class:`RegionSpec` for ``region_id``.

        Raises :class:`KeyError` when the region is unknown.
        """
        with self._lock:
            region = self._regions.get(region_id)
        if region is None:
            raise KeyError(region_id)
        return region

    async def local_region(self) -> RegionSpec:
        """Return the :class:`RegionSpec` for the local (co-located) region.

        Raises :class:`RuntimeError` when no region is marked local.
        """
        with self._lock:
            for region in self._regions.values():
                if region.is_local:
                    return region
        raise RuntimeError("no local region configured")

    async def mark_unavailable(self, region_id: str) -> None:
        """Mark a region as unreachable.  Idempotent."""
        with self._lock:
            region = self._regions.get(region_id)
            if region is not None:
                self._regions[region_id] = dataclasses.replace(
                    region, available=False
                )

    async def mark_available(self, region_id: str) -> None:
        """Mark a region as reachable again.  Idempotent."""
        with self._lock:
            region = self._regions.get(region_id)
            if region is not None:
                self._regions[region_id] = dataclasses.replace(
                    region, available=True
                )
