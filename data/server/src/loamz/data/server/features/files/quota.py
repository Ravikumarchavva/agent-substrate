"""Per-volume byte quotas. Pure arithmetic; the decision is made inside the committing transaction."""

from __future__ import annotations

from loamz.data.storage.errors import QuotaExceededError


def projected_bytes(used: int, adding: int, replacing: int = 0) -> int:
    """Live bytes after a write that adds `adding` bytes and supersedes `replacing` bytes."""
    return max(0, used - replacing) + adding


def check_quota(used: int, adding: int, quota: int | None, replacing: int = 0) -> None:
    """Raise `413 quota-exceeded` if the write would push live bytes past a hard quota."""
    if quota is None:
        return
    after = projected_bytes(used, adding, replacing)
    if after > quota:
        raise QuotaExceededError(
            f"The volume's storage quota is exceeded: {after} bytes would be stored, quota is {quota}",
            limit=quota,
            used=used,
        )
