"""Wire protocol version.

Bump this whenever ``events.py`` or ``requests.py`` change shape. The UI mirrors
this constant and asserts it against the ``protocol.hello`` event at stream start,
so a forgotten codegen surfaces immediately instead of as silent field drift.
"""

from __future__ import annotations

PROTOCOL_VERSION = "1.0.0"

__all__ = ["PROTOCOL_VERSION"]
