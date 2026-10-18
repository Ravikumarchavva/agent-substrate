"""Job bodies for the memory-limit tests; importable by name so a worker's forked child can run them."""

from __future__ import annotations

import time


def runaway(pipe, settings, operation, payload) -> None:
    """Grows without bound, touching every page so the memory is really resident."""
    held = []
    while True:
        held.append(bytearray(b"x") * (64 * 2**20))
        time.sleep(0.02)


def polite(pipe, settings, operation, payload) -> None:
    block = bytearray(b"x") * (20 * 2**20)
    pipe.send(("ok", {"bytes": len(block)}))
    pipe.close()
