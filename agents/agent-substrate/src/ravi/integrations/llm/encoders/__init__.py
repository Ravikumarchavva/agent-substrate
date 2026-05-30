"""Per-provider message encoding.

Converts framework messages to API-specific formats. Each encoder module
exposes ``encode_messages`` and ``encode_tools``.

The ``storage`` encoder serialises messages to a neutral JSON format for
persistence in Redis / Postgres (round-trip safe, no provider coupling).
"""

from __future__ import annotations
