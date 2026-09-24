"""Fit a conversation to what a model can actually see.

Every ``LLMClient`` runs its messages through ``fit_to_capabilities`` before
encoding: any ``MediaBlock`` — in a user turn or inside a tool result — whose
modality isn't in ``capabilities.input_modalities`` is replaced by a short
text note, so a text-only model is told something was there instead of the
provider rejecting the request or the content vanishing silently.
"""

from __future__ import annotations

from substrate.kernel.core.content import (
    ChatMessage,
    ContentBlock,
    MediaBlock,
    TextBlock,
    ToolResultBlock,
)
from substrate.kernel.llm import ModelCapabilities, Modality


def _placeholder(block: MediaBlock, caps: ModelCapabilities) -> TextBlock:
    note = f"[{block.type} not shown: {caps.model_id} cannot take {block.type} input"
    if block.filename:
        note += f"; file: {block.filename}"
    if block.transcript:
        note += f"; transcript: {block.transcript}"
    return TextBlock(text=note + "]")


def _fit_blocks(
    blocks: list[ContentBlock], caps: ModelCapabilities
) -> tuple[list[ContentBlock], bool]:
    out: list[ContentBlock] = []
    changed = False
    for block in blocks:
        if isinstance(block, MediaBlock) and not caps.accepts(Modality(block.type)):
            out.append(_placeholder(block, caps))
            changed = True
        elif isinstance(block, ToolResultBlock):
            inner, inner_changed = _fit_blocks(list(block.content), caps)
            if inner_changed:
                block = block.model_copy(update={"content": inner})
                changed = True
            out.append(block)
        else:
            out.append(block)
    return out, changed


def fit_to_capabilities(
    messages: list[ChatMessage], caps: ModelCapabilities
) -> list[ChatMessage]:
    fitted: list[ChatMessage] = []
    for msg in messages:
        blocks, changed = _fit_blocks(list(msg.content), caps)
        fitted.append(msg.model_copy(update={"content": blocks}) if changed else msg)
    return fitted


__all__ = ["fit_to_capabilities"]
