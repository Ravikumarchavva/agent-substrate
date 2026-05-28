"""Shared media encoding utilities for provider-specific message encoders.

All three encoders (OpenAI, Anthropic, Gemini) need the same PIL → bytes
and bytes → base64 conversions. Centralizing them here avoids duplication.
"""

from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def pil_to_png_bytes(img: "Image.Image") -> bytes:
    """Encode a PIL Image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def bytes_to_base64(data: bytes) -> str:
    """Encode raw bytes to a base64 string."""
    return base64.b64encode(data).decode("utf-8")


def pil_to_base64_png(img: "Image.Image") -> str:
    """Encode a PIL Image to a base64 PNG string."""
    return bytes_to_base64(pil_to_png_bytes(img))
