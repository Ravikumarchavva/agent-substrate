"""Dump the wire protocol as a single JSON Schema for TypeScript codegen.

Run::

    uv run python -m agent_substrate.serving.protocol.export

Writes ``protocol.schema.json`` next to this file. The UI's ``pnpm gen:protocol``
reads that file and generates ``src/protocol/protocol.gen.ts``. Re-run both
whenever ``events.py`` or ``requests.py`` change.

The schema bundles every wire event (via the ``WireEvent`` discriminated union)
and every request body under a single top-level object with ``$defs`` so one
codegen pass produces all the TypeScript interfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from agent_substrate.serving.protocol.events import WireEvent
from agent_substrate.serving.protocol.requests import (
    ApprovalResponse,
    CancelRequest,
    ChatRequest,
    InputResponse,
)
from agent_substrate.serving.protocol.version import PROTOCOL_VERSION

_SCHEMA_PATH = Path(__file__).parent / "protocol.schema.json"


def build_schema() -> dict:
    """Build one JSON Schema document covering events + requests."""
    event_schema = TypeAdapter(WireEvent).json_schema(ref_template="#/$defs/{model}")
    # The event union schema carries its component models in ``$defs``; merge the
    # request models in alongside so a single codegen run emits everything.
    defs: dict = dict(event_schema.get("$defs", {}))
    for model in (ChatRequest, ApprovalResponse, InputResponse, CancelRequest):
        defs[model.__name__] = model.model_json_schema(ref_template="#/$defs/{model}")

    # Top-level wrapper: a "WireEvent" property holding the union, plus all defs.
    union_no_defs = {k: v for k, v in event_schema.items() if k != "$defs"}
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "RaviProtocol",
        "x-protocol-version": PROTOCOL_VERSION,
        "type": "object",
        "properties": {"WireEvent": union_no_defs},
        "$defs": defs,
    }


def main() -> None:
    schema = build_schema()
    _SCHEMA_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Wrote {_SCHEMA_PATH} (protocol v{PROTOCOL_VERSION})")


if __name__ == "__main__":
    main()
