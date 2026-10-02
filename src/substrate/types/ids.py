"""Identifiers.

Two things in one place so they cannot drift apart again:

* **Distinct types for distinct ids.** A run id, a thread id and a tenant id
  are all strings, and all of them are passed positionally to functions that
  take several. ``NewType`` costs nothing at runtime and makes passing the
  wrong one a type error instead of a silent cross-wiring.
* **One generator.** ``new_id`` produces UUIDv7: the leading 48 bits are a
  millisecond timestamp, so ids sort in creation order. That is what lets a
  log, an inbox or a primary-key index stay ordered without a separate
  sequence column, and it keeps B-tree inserts append-mostly.

The previous code minted ids with ``uuid4`` in two spellings (hex and dashed)
at twenty-odd call sites; nothing sorted and nothing was consistent.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import NewType

TenantId = NewType("TenantId", str)
UserId = NewType("UserId", str)
ThreadId = NewType("ThreadId", str)
"""The one name for "a conversation": what owns files, documents, task boards
and history. (The old code had three — ``session_id``, ``thread_id`` and
``conversation_id`` — for the same thing.)"""
RunId = NewType("RunId", str)
BranchId = NewType("BranchId", str)
MessageId = NewType("MessageId", str)

_lock = threading.Lock()
_last_ms = -1
_last_counter = 0


def new_id() -> str:
    """A fresh UUIDv7, as 32 lowercase hex characters.

    Strictly increasing within a process: two ids minted in the same
    millisecond are ordered by a counter in the ``rand_a`` field, so ordering
    never depends on the clock's resolution.
    """
    global _last_ms, _last_counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            _last_counter = secrets.randbits(11)  # headroom before the counter wraps
        else:
            # Same millisecond, or the clock stepped back: keep ordering by
            # advancing the counter, and borrow a millisecond if it overflows.
            _last_counter += 1
            if _last_counter >= 1 << 12:
                _last_ms += 1
                _last_counter = 0
        ms, counter = _last_ms, _last_counter
    rand_b = secrets.randbits(62)
    value = (
        (ms & 0xFFFF_FFFF_FFFF) << 80
        | 0x7 << 76
        | (counter & 0xFFF) << 64
        | 0b10 << 62
        | rand_b
    )
    return f"{value:032x}"


def new_run_id() -> RunId:
    return RunId(new_id())


__all__ = [
    "BranchId",
    "MessageId",
    "RunId",
    "TenantId",
    "ThreadId",
    "UserId",
    "new_id",
    "new_run_id",
]
