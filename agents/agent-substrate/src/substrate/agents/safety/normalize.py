"""Text de-obfuscation for the safety pipeline — runs before every text
classifier call, chat and document alike.

Non-ML, deterministic, pure-stdlib-plus-one-tiny-package (``confusable_homoglyphs``,
zero transitive deps). This is deliberately NOT folded into NFKC: NFKC only
folds *compatibility* forms (fullwidth, ligatures, ⓐ-style circled letters)
— it does **not** map Cyrillic 'і' (U+0456) to Latin 'i'. Those are two
different Unicode mechanisms solving two different problems; treating NFKC
as a homoglyph defense is a real, common mistake this module exists to not
make (see ``docs`` / the plan this was built from).

The actual homoglyph defense is the UTS-39 "confusables" table (via
``confusable_homoglyphs``): for each character, ask what it's confusable
with in the LATIN block, and if it's not itself Latin/Common, substitute the
Latin form to build a "skeleton" string. A classifier scores the skeleton;
the caller decides whether to also score the original.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from confusable_homoglyphs import confusables

# ── Character classes that are invisible or near-invisible to a human but
# meaningful to a tokenizer — each is its own smuggling channel. ────────────
_ZERO_WIDTH = "​‌‍﻿"  # ZWSP, ZWNJ, ZWJ, BOM/ZWNBSP
_BIDI_CONTROLS = "‪‫‬‭‮⁦⁧⁨⁩"
# Unicode "tag characters" — U+E0001, U+E0020-E007F. Invisible, but valid
# codepoints an LLM tokenizer will happily encode; a documented real-world
# prompt-smuggling channel (ASCII tags mirrored into these codepoints).
_TAG_CHARS_RE = re.compile(r"[\U000E0000-\U000E007F]")
_ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH}]")
_BIDI_RE = re.compile(f"[{_BIDI_CONTROLS}]")
_WHITESPACE_RUN_RE = re.compile(r"\s{4,}")
_BASE64_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")

# LATIN/COMMON are the "not suspicious" scripts — anything else that has a
# Latin-block confusable is a homoglyph-substitution candidate.
_UNSUSPICIOUS_ALIASES = {"LATIN", "COMMON"}


@dataclass(frozen=True)
class NormalizedText:
    """Result of running :func:`normalize` on one piece of input.

    ``text`` is NFKC-folded with invisible/control characters stripped —
    always safe to feed a classifier. ``skeleton`` additionally has
    cross-script homoglyphs mapped to their Latin form — score BOTH; an
    attack can be built to look identical to its skeleton (no evasion, no
    signal loss) or to differ from it (evasion, the skeleton is what catches
    it). ``evasion_signals`` is itself a weak flag: a high count is
    suspicious even when both classifier scores come back benign.
    """

    text: str
    skeleton: str
    evasion_signals: list[str] = field(default_factory=list)
    decoded_base64: str | None = None


@lru_cache(maxsize=4096)
def _latin_skeleton_char(ch: str) -> str:
    """The Latin homoglyph for one character, or the character unchanged if
    it's already Latin/Common or has no Latin confusable. Cached — the same
    handful of confusable characters recur across many messages."""
    if ch.isascii():
        return ch
    try:
        matches = confusables.is_confusable(ch, greedy=True)
    except Exception:
        # confusable_homoglyphs raises on some exotic/unassigned codepoints
        # (e.g. private-use area) — fail open to the original character
        # rather than let one bad char break normalization for the whole
        # message.
        return ch
    if not matches:
        return ch
    entry = matches[0]
    if entry.get("alias") in _UNSUSPICIOUS_ALIASES:
        return ch
    for homoglyph in entry.get("homoglyphs", []):
        candidate = homoglyph.get("c", "")
        name = homoglyph.get("n", "")
        if len(candidate) == 1 and candidate.isascii() and "LATIN" in name:
            return candidate
    return ch


def _build_skeleton(text: str) -> tuple[str, bool]:
    out_chars: list[str] = []
    changed = False
    for ch in text:
        skel_ch = _latin_skeleton_char(ch)
        if skel_ch != ch:
            changed = True
        out_chars.append(skel_ch)
    return "".join(out_chars), changed


def _decode_base64_blobs(text: str) -> str | None:
    """Decode any long base64-looking substring that decodes to mostly-printable
    text — attackers stash instructions there to dodge a plain-text scan.
    Returns a string of decoded fragments (joined), or None if nothing
    decoded cleanly."""
    decoded_parts: list[str] = []
    for match in _BASE64_CANDIDATE_RE.finditer(text):
        candidate = match.group()
        try:
            raw = base64.b64decode(candidate, validate=True)
        except Exception:
            continue
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        printable = sum(1 for c in decoded if c.isprintable() or c.isspace())
        if not decoded or printable / len(decoded) < 0.9:
            continue
        decoded_parts.append(decoded)
    return "\n".join(decoded_parts) if decoded_parts else None


def normalize(text: str) -> NormalizedText:
    """De-obfuscate ``text`` for safety classification. Cheap (microseconds
    for typical chat-message lengths) and fully deterministic."""
    signals: list[str] = []

    zero_width_count = len(_ZERO_WIDTH_RE.findall(text))
    if zero_width_count:
        signals.append(f"zero_width:{zero_width_count}")

    bidi_count = len(_BIDI_RE.findall(text))
    if bidi_count:
        signals.append(f"bidi_control:{bidi_count}")

    tag_count = len(_TAG_CHARS_RE.findall(text))
    if tag_count:
        signals.append(f"tag_chars:{tag_count}")

    cleaned = _ZERO_WIDTH_RE.sub("", text)
    cleaned = _BIDI_RE.sub("", cleaned)
    cleaned = _TAG_CHARS_RE.sub("", cleaned)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = _WHITESPACE_RUN_RE.sub(" ", cleaned)

    skeleton, changed = _build_skeleton(cleaned)
    if changed:
        signals.append("homoglyph_substitution")

    if changed and any(c.isascii() and c.isalpha() for c in cleaned):
        signals.append("mixed_script")

    decoded = _decode_base64_blobs(text)
    if decoded is not None:
        signals.append("base64_payload")

    return NormalizedText(
        text=cleaned,
        skeleton=skeleton,
        evasion_signals=signals,
        decoded_base64=decoded,
    )


__all__ = ["NormalizedText", "normalize"]
