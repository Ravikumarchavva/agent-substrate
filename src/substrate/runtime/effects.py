"""Effect and EffectResult — at-most-once guarantee for external side-effects.

Why this exists
---------------
You cannot make a real-world side-effect (send email, charge card, call an
external API) atomic with your event log — the external service doesn't
participate in your transaction.  Journaling ``EffectResult`` entries straight
into the EventLogProtocol provides the closest safe approximation: **at-most-once
execution** via an idempotent lookup.

Protocol on every effect
------------------------
1. Compute ``effect_id = hash(run_id + path + kind + canonical(args))``.
   It is deterministic — the same logical step in the same run always
   produces the same id. ``path`` is hierarchical (see ``Effect.make_id``),
   not a flat counter, so a journal-hit ancestor whose body never executes
   cannot desync the ids of everything that comes after it.
2. ``EffectCache.lookup(effect_id)`` (folded from the EventLogProtocol — see
   ``agents/runtime/effect_cache.py``) → hit → return cached result, **do not
   re-run**.
3. Miss → execute the effect → append an ``effect.result`` log entry.

Crash window: if the worker dies after step 3's *execute* but before the log
append, the effect may have happened without a journal entry.  On replay the
miss path runs again — so the effect executes twice in that rare window.  This
is the unavoidable trade-off: **at-most-once** means we do NOT retry on that
uncertainty, so you never double-send, but a crash in that window may silently
lose the effect.

Tools that are genuinely idempotent (e.g. a GET, a Stripe charge with the
same idempotency key) can be safely retried; they should document this in
their ``Tool.description`` so callers know they can rely on exactly-once
semantics instead.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from substrate.kernel.abstractions.core.content import JsonObject
from substrate.kernel.abstractions.runtime.ids import RunId


def _canonical_default(value: Any) -> Any:
    """Encode the non-JSON values tool arguments routinely contain, deterministically.

    ``json.dumps`` raises on bytes and datetimes, and effect identity is computed
    from arguments before every call. Anything not listed here is refused rather
    than stringified, because a ``repr`` embedding an object address would give
    the same call a different identity on every replay.
    """
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted(value, key=repr)}
    raise TypeError(f"{type(value).__name__} has no canonical encoding for effect identity")


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, deterministic for any supported value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_canonical_default)


def args_digest(args: Any) -> str:
    """A short digest of a call's arguments, journaled with the effect so a replay
    that arrives with different arguments is detected instead of served the old
    call's result."""
    return hashlib.sha256(canonical_json(args).encode()).hexdigest()[:32]

class Effect(BaseModel):
    """A description of an external side-effect to be executed at-most-once.

    ``id`` is deterministic: derived from the run's context + step position +
    effect kind + canonical argument representation.  Use ``Effect.make_id()``
    to compute it consistently.

    ``kind`` is a dot-namespaced string matching the tool or operation name
    (e.g. ``"email.send"``, ``"stripe.charge"``, ``"db.insert"``).

    ``spec`` carries the arguments needed to execute the effect — a JSON-
    serializable dict so it can be logged for audit replay.
    """

    id: str
    kind: str
    spec: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}

    @staticmethod
    def make_id(
        run_id: RunId,
        path: str,
        kind: str,
        args: JsonObject,
    ) -> str:
        """Deterministically compute an effect id.

        ``path`` is a hierarchical position, not a flat counter — e.g. ``"2"``
        for the 3rd top-level journaled call, ``"2.0"`` for the 1st journaled
        call made *inside* that call's body (only tool calls open a nested
        scope; see ``RunContext``). This is required for replay correctness:
        if a call is a journal hit, its body (and any journaled calls inside
        it) never executes, so no indices are consumed at that nested level —
        only the single index for the call itself. A flat run-wide counter
        would desync from the second nested effect onward on any replay where
        an ancestor was a cache hit. Callers with no nesting concern (LLM
        calls, deterministic helpers) just pass a single-segment path.

        Canonical: sorts dict keys before hashing so argument order doesn't
        matter.  Returns a 16-char hex prefix of SHA-256.
        """
        raw = canonical_json(
            {"run_id": run_id, "path": path, "kind": kind, "args": args}
        ).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


class EffectResult(BaseModel):
    """Cached result of a completed effect execution.

    Logged to the EventLogProtocol as an ``effect.result`` entry immediately after the
    external call returns. On replay, ``EffectCache.fold()`` (built from these
    entries — see ``agents/runtime/effect_cache.py``) returns this instead of
    re-running the effect.

    ``artifact_ref`` is set when the result was offloaded to ``ArtifactStore``
    (e.g. a 200 MB query result) — the log entry stores only the ref.
    """

    effect_id: str
    status: Literal["ok", "error"]
    value: JsonObject = Field(default_factory=dict)
    artifact_ref: str | None = None

    model_config = {"frozen": True}


__all__ = ["Effect", "EffectResult", "args_digest", "canonical_json"]
