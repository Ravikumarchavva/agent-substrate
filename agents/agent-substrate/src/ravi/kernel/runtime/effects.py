"""Effect and Journal — at-most-once guarantee for external side-effects.

Why this exists
---------------
You cannot make a real-world side-effect (send email, charge card, call an
external API) atomic with your event log — the external service doesn't
participate in your transaction.  The Journal provides the closest safe
approximation: **at-most-once execution** via an idempotent lookup.

Protocol on every effect
------------------------
1. Compute ``effect_id = hash(run_id + step_seq + kind + canonical(args))``.
   It is deterministic — the same logical step in the same run always
   produces the same id.
2. ``Journal.lookup(effect_id)`` → hit → return cached result, **do not re-run**.
3. Miss → execute the effect → ``Journal.record(result)``.

Crash window: if the worker dies after step 3's *execute* but before *record*,
the effect may have happened without a journal entry.  On replay the miss path
runs again — so the effect executes twice in that rare window.  This is the
unavoidable trade-off: **at-most-once** means we do NOT retry on that
uncertainty, so you never double-send, but a crash in that window may silently
lose the effect.

Tools that are genuinely idempotent (e.g. a GET, a Stripe charge with the
same idempotency key) can be safely retried; they should document this in
their ``Tool.description`` so callers know they can rely on exactly-once
semantics instead.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ravi.kernel.core.content import JsonObject
from ravi.kernel.runtime.ids import RunId


class Effect(BaseModel):
    """A description of an external side-effect to be executed at-most-once.

    ``id`` is deterministic: derived from the run's context + step position +
    effect kind + canonical argument representation.  Use ``Effect.make_id()``
    to compute it consistently.

    ``kind`` is a dot-namespaced string matching the tool or operation name
    (e.g. ``"email.send"``, ``"stripe.charge"``, ``"db.insert"``).

    ``spec`` carries the arguments needed to execute the effect — a JSON-
    serializable dict so the Journal can store it for audit replay.
    """

    id: str
    kind: str
    spec: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}

    @staticmethod
    def make_id(
        run_id: RunId,
        step_seq: int,
        kind: str,
        args: JsonObject,
    ) -> str:
        """Deterministically compute an effect id.

        Canonical: sorts dict keys before hashing so argument order doesn't
        matter.  Returns a 16-char hex prefix of SHA-256.
        """
        raw = json.dumps(
            {"run_id": run_id, "step_seq": step_seq, "kind": kind, "args": args},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


class EffectResult(BaseModel):
    """Cached result of a completed effect execution.

    Stored in the Journal immediately after the external call returns.
    On replay, ``Journal.lookup(effect_id)`` returns this instead of
    re-running the effect.

    ``artifact_ref`` is set when the result was offloaded to ``ArtifactStore``
    (e.g. a 200 MB query result) — the Journal stores only the ref.
    """

    effect_id: str
    status: Literal["ok", "error"]
    value: JsonObject = Field(default_factory=dict)
    artifact_ref: str | None = None

    model_config = {"frozen": True}


class Journal(Protocol):
    """Idempotency cache for external effects.

    Implementations: in-memory dict (Stage 0), Postgres table keyed by
    ``effect_id`` (Stage 1), Redis with TTL (hot path Stage 2+).

    Semantic guarantees
    -------------------
    - ``lookup`` is idempotent and read-only.
    - ``record`` is a write-once operation: recording an already-recorded
      effect_id is a no-op (the first result wins — never overwritten).
    - Both operations are safe to call concurrently across workers.
    """

    async def lookup(self, effect_id: str) -> EffectResult | None:
        """Return the cached result for ``effect_id``, or ``None`` on miss."""
        ...

    async def record(self, result: EffectResult) -> None:
        """Persist ``result`` for its ``effect_id``.

        Write-once: if ``effect_id`` already has a recorded result, this is
        a no-op.  Never raises on duplicate — callers do not need to check.
        """
        ...


__all__ = ["Effect", "EffectResult", "Journal"]
