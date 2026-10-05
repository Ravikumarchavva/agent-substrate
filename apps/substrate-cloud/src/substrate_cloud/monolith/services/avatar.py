"""The picture of an agent or a group.

Whatever is sent is decoded and drawn again as a 256-pixel square PNG (centre-cropped, rotated upright, nothing of the original kept: no metadata,
no script), and that is what is stored. Only PNG, JPEG and WebP are accepted; SVG can carry script and is refused.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from substrate.workspace.layout import workspace_avatar_key

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SIZE = 256
_MAX_PIXELS = 40_000_000
_FORMATS = {"PNG", "JPEG", "WEBP"}


class AvatarError(ValueError):
    """The upload is not a picture we keep."""


def render(data: bytes) -> bytes:
    """The stored picture for an upload: a ``SIZE`` square PNG."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise AvatarError("That picture is larger than 5 MB.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in _FORMATS:
                raise AvatarError("Use a PNG, JPEG or WebP picture.")
            if image.width * image.height > _MAX_PIXELS:
                raise AvatarError("That picture is too large.")
            upright = ImageOps.exif_transpose(image)
            square = ImageOps.fit(upright.convert("RGBA"), (SIZE, SIZE), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AvatarError("That is not a picture we can read.") from exc
    out = io.BytesIO()
    square.save(out, "PNG", optimize=True)
    return out.getvalue()


async def replace(store: Any, tenant_id: str, user_id: str, workspace_id: str, previous: str | None, data: bytes) -> str:
    """Store the picture made from ``data`` for a workspace's owner and delete the one it replaces. Returns its key."""
    png = render(data)
    key = workspace_avatar_key(tenant_id, user_id, workspace_id, hashlib.sha256(png).hexdigest()[:32], "png")
    await store.upload(key, png, content_type="image/png")
    if previous and previous != key:
        await store.delete(previous)
    return key


async def remove(store: Any, previous: str | None) -> None:
    if previous:
        await store.delete(previous)
