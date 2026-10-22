"""Cache-affinity routing: the same tables go to the same worker, and only a lost worker's keys move."""

from __future__ import annotations

import hashlib
from collections import Counter

from loamz.server.data.features.sql.api import rendezvous

WORKERS = [{"id": f"worker-{i}"} for i in range(5)]


def keys(n=2000):
    return [hashlib.sha1(str(i).encode()).hexdigest() for i in range(n)]


def test_a_key_always_lands_on_the_same_worker():
    assert all(rendezvous(WORKERS, k) == rendezvous(WORKERS, k) for k in keys(200))


def test_work_spreads_evenly_across_workers():
    spread = Counter(rendezvous(WORKERS, k)["id"] for k in keys())
    assert len(spread) == 5 and max(spread.values()) < 2 * min(spread.values())


def test_losing_a_worker_moves_only_the_keys_it_owned():
    survivors = WORKERS[:-1]
    moved = [
        k
        for k in keys()
        if rendezvous(WORKERS, k)["id"] != "worker-4"
        and rendezvous(WORKERS, k) != rendezvous(survivors, k)
    ]
    assert moved == []  # every key that did not live on the lost worker stays where it was


def test_a_single_worker_gets_everything():
    assert {rendezvous(WORKERS[:1], k)["id"] for k in keys(50)} == {"worker-0"}
