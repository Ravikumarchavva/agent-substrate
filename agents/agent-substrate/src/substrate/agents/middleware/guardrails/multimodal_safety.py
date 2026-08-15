"""Multimodal input safety guardrail — TURN stage, group-evaluated.

Evaluates every content block of the turn's *last* message together: each
``TextBlock`` (normalized first) through a ``TextSafetyClassifier``, each
``ImageBlock`` with directly-available bytes through an
``ImageSafetyClassifier``. The turn's verdict is the max severity across
every block and every detector — "in group," per the design: a benign text
caption next to a flagged image still flags the whole turn.

A cheap regex layer (the existing, previously-unused ``_INJECTION_PATTERNS``
from ``prompt_injection.py``) runs *before* the model on each text block —
free, and catches blatant known phrasings a semantic classifier can
genuinely miss. Concretely verified during this implementation: Prompt
Guard scored "developer mode... do anything now" at only 0.3% malicious —
a near-miss on a very well-known jailbreak template — while the regex
pattern for exactly that phrase already existed in this codebase, unused.
Defense in depth, not redundancy.

Persist-but-exclude: by the time this middleware runs, ``ReActAgent.
_handle_message()`` has already durably logged the user's message
(``log_user_message`` runs before the middleware pipeline — see
``agents/core/react.py``) — so "persists in thread history" is already true
for free. This middleware's job is only the other two-thirds: (1) journal a
companion ``user.message.flagged`` marker referencing that exact message's
seq, for admin visibility and for ``agents/factory.py::step_rows_from_log``
to redact it from future turns' context, and (2) halt the turn via
``MiddlewareTermination`` *before* the LLM is ever called — the model never
sees the flagged content on this turn, no LLM spend, no exception to that.

Known, named gap (not silently dropped): ``ImageBlock`` can reference
``url``/``file_id`` without inline ``data`` — this guardrail only classifies
blocks with directly-available bytes. OCR-on-chat-images (catching T4,
cross-modal injection rendered as pixels, on the *live chat* path — the
document-upload path's own OCR cascade is separate and unaffected) requires
an async call to the extraction service and is deliberately not built in
this pass; a block this guardrail can't evaluate is treated as unable-to-
verify, not as flagged (fail-open on a genuine capability gap, not a
security bypass of something we could check).
"""

from __future__ import annotations

import asyncio
from typing import Callable, Awaitable, ClassVar

from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.agents.middleware.guardrails.prompt_injection import (
    _INJECTION_PATTERNS,
)
from substrate.agents.safety.normalize import normalize
from substrate.exceptions import MiddlewareTermination
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.kernel.agent.safety import (
    ImageSafetyClassifier,
    SafetyVerdict,
    Severity,
    TextSafetyClassifier,
    max_severity,
)
from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.logger import setup_logging

logger = setup_logging("substrate.agents.middleware.multimodal_safety")


def _regex_verdict(text: str) -> SafetyVerdict:
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            return SafetyVerdict(
                severity=Severity.HIGH,
                scores={"malicious": 1.0},
                detector="regex",
                modality="text",
                detail=f"matched pattern: {match.group()[:60]!r}",
            )
    return SafetyVerdict(severity=Severity.NONE, detector="regex", modality="text")


class MultimodalSafetyMiddleware:
    """TURN-stage guardrail: prompt-attack (text) + NSFW/NSFL (image),
    group-evaluated, persist-but-exclude semantics.

    ``text_classifier``/``image_classifier`` are the kernel Protocols
    (``kernel/agent/safety.py``) — concrete instances (``PromptGuardClassifier``/
    ``ImageSafetyClassifier``) are constructed and injected from
    ``infrastructure/serving_factory.py``, the orthogonal module allowed to
    import both L1 (this) and L2 (the concrete classifiers) — this module
    itself never imports a concrete classifier, only the Protocol.
    """

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TURN})

    def __init__(
        self,
        *,
        text_classifier: TextSafetyClassifier,
        image_classifier: ImageSafetyClassifier | None = None,
    ) -> None:
        self._text_classifier = text_classifier
        self._image_classifier = image_classifier

    async def _classify_text(self, text: str) -> SafetyVerdict:
        normalized = normalize(text)

        # Checked against BOTH the raw normalized text and the homoglyph-
        # de-evaded skeleton — an ASCII regex pattern can never match
        # un-substituted Cyrillic/Greek/etc. lookalikes, so skeleton-only
        # checking would silently let a homoglyph-evaded jailbreak skip
        # this entire layer and fall through to (weaker) evasion-signal
        # escalation instead of the strong regex match it should get.
        regex_hit = _regex_verdict(normalized.text)
        if not regex_hit.flagged:
            regex_hit = _regex_verdict(normalized.skeleton)
        if regex_hit.flagged:
            return regex_hit

        # Score the skeleton (homoglyph-normalized) — an evasion attempt is
        # exactly the case where `text` and `skeleton` differ; scoring only
        # the raw original would miss it.
        model_verdict = await asyncio.to_thread(
            self._text_classifier.classify, normalized.skeleton
        )
        if normalized.evasion_signals and not model_verdict.flagged:
            # Evasion signals present but the model itself didn't flag it —
            # still worth a low-severity signal for the audit trail (tuning
            # data), not a block. A high zero-width/tag-char count on
            # otherwise-benign text is unusual enough to be worth recording,
            # not enough alone to halt a turn.
            return SafetyVerdict(
                severity=Severity.LOW,
                scores=model_verdict.scores,
                detector=model_verdict.detector,
                modality="text",
                detail=f"evasion_signals={normalized.evasion_signals}",
            )
        return model_verdict

    async def _classify_image(self, block: ImageBlock) -> SafetyVerdict:
        if self._image_classifier is None or block.data is None:
            return SafetyVerdict(
                severity=Severity.NONE, detector="image_safety", modality="image"
            )
        return await asyncio.to_thread(self._image_classifier.classify, block.data)

    async def process(
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        messages = context.messages or []
        if not messages:
            await call_next()
            return

        last_msg = messages[-1]
        verdicts: list[SafetyVerdict] = []
        for block in last_msg.content:
            if isinstance(block, TextBlock) and block.text.strip():
                verdicts.append(await self._classify_text(block.text))
            elif isinstance(block, ImageBlock):
                verdicts.append(await self._classify_image(block))

        if not verdicts:
            await call_next()
            return

        worst = verdicts[0]
        for v in verdicts[1:]:
            if max_severity(worst.severity, v.severity) != worst.severity:
                worst = v

        if worst.flagged:
            if context.run_context is not None and context.user_message_seq is not None:
                await context.run_context.log_once(
                    "user.message.flagged",
                    {
                        "seq": context.user_message_seq,
                        "detector": worst.detector,
                        "severity": worst.severity.value,
                        "scores": worst.scores,
                        "modality": worst.modality,
                    },
                )
            logger.warning(
                "MultimodalSafetyMiddleware: turn flagged (detector=%s severity=%s)",
                worst.detector,
                worst.severity.value,
            )
            raise MiddlewareTermination(
                f"Safety: message flagged by {worst.detector} "
                f"(severity={worst.severity.value}) and was not processed."
            )

        await call_next()


__all__ = ["MultimodalSafetyMiddleware"]
