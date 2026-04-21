"""Per-provider message encoding — converts framework messages to API-specific formats.

Each encoder module exposes two functions:

    encode_messages(messages) → provider-specific conversation input
    encode_tools(tools)       → provider-specific tool definitions

Usage::

    from ravi.core.messages.encoders.openai import encode_messages, encode_tools

The ``storage`` encoder serialises messages to a neutral JSON format for
persistence in Redis / Postgres (round-trip safe, no provider coupling).
"""

from __future__ import annotations
