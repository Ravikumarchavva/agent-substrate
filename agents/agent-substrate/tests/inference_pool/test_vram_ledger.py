"""``VramLedger`` — pure in-memory arithmetic, no GPU/CUDA needed."""

from __future__ import annotations

import asyncio

from substrate.runtimes.inference_pool.vram_ledger import VramLedger


async def test_reserve_within_budget_succeeds_and_deducts():
    ledger = VramLedger({"gpu:0": 4096})

    lease = await ledger.reserve("gpu:0", 3500)

    assert lease is not None
    assert lease.device == "gpu:0"
    assert lease.mib == 3500
    assert ledger.available("gpu:0") == 596


async def test_reserve_over_budget_returns_none_and_does_not_deduct():
    ledger = VramLedger({"gpu:0": 4096})

    lease = await ledger.reserve("gpu:0", 5000)

    assert lease is None
    assert ledger.available("gpu:0") == 4096


async def test_release_credits_the_budget_back():
    ledger = VramLedger({"gpu:0": 4096})
    lease = await ledger.reserve("gpu:0", 3500)
    assert lease is not None

    await ledger.release(lease)

    assert ledger.available("gpu:0") == 4096


async def test_two_workers_share_one_gpus_budget_second_fails_when_it_does_not_fit():
    """The real scenario this exists for: two consumers in one process
    (e.g. layout detection + a llama-server pool worker) contending for one
    GPU's real budget."""
    ledger = VramLedger({"gpu:0": 4096})

    first = await ledger.reserve("gpu:0", 3500)
    second = await ledger.reserve("gpu:0", 3500)

    assert first is not None
    assert second is None  # only 596 MiB left, doesn't fit a second 3500 reservation


async def test_unknown_device_has_zero_available_and_every_reserve_fails():
    ledger = VramLedger({"gpu:0": 4096})

    assert ledger.available("gpu:1") == 0
    assert await ledger.reserve("gpu:1", 1) is None


async def test_reserve_zero_mib_always_succeeds_even_at_zero_budget():
    ledger = VramLedger({"gpu:0": 0})

    lease = await ledger.reserve("gpu:0", 0)

    assert lease is not None
    assert ledger.available("gpu:0") == 0


async def test_concurrent_reserves_never_overcommit_the_budget():
    """Real concurrency, not just sequential calls -- asyncio.Lock must
    actually serialize the check-then-deduct, or two concurrent reserve()
    calls could both read the same 'available' value before either
    deducts."""
    ledger = VramLedger({"gpu:0": 4096})

    results = await asyncio.gather(*(ledger.reserve("gpu:0", 3000) for _ in range(3)))

    granted = [r for r in results if r is not None]
    assert len(granted) == 1  # only one 3000 MiB reservation fits in 4096
    assert ledger.available("gpu:0") == 1096
