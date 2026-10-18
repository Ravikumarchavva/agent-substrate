"""Per-volume byte quotas. Pure arithmetic; the decision is made inside the committing transaction."""

from __future__ import annotations

from substrate_data.core.errors import Problem


def projected_bytes(used: int, adding: int, replacing: int = 0) -> int:
    """Live bytes after a write that adds `adding` bytes and supersedes `replacing` bytes."""
    return max(0, used - replacing) + adding


def check_quota(used: int, adding: int, quota: int | None, replacing: int = 0) -> None:
    """Raise `413 quota-exceeded` if the write would push live bytes past a hard quota."""
    if quota is None:
        return
    after = projected_bytes(used, adding, replacing)
    if after > quota:
        raise Problem(
            413,
            "quota-exceeded",
            f"The volume's storage quota is exceeded: {after} bytes would be stored, quota is {quota}",
            used_bytes=used,
            requested_bytes=adding,
            quota_bytes=quota,
        )
