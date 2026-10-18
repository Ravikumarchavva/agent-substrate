"""The picture of an agent or a group.

Whatever is sent is decoded and drawn again as a 256-pixel square PNG (centre-cropped, rotated upright, nothing of
the original kept: no metadata, no script), and that is what is stored. Only PNG, JPEG and WebP are accepted; SVG can
carry script and is refused. The bytes go to `container.file_store` beside the owner's workspace.
"""

from __future__ import annotations

import hashlib
import io

from loamz.agents.bootstrap.container import Container
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.stores.workspace.layout import workspace_avatar_key
from loamz.data.storage.errors import InvalidRequestError, QuotaExceededError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SIZE = 256
_MAX_PIXELS = 40_000_000
_FORMATS = {"PNG", "JPEG", "WEBP"}


def render(data: bytes) -> bytes:
    """The stored picture for an upload: a `SIZE` square PNG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if len(data) > MAX_UPLOAD_BYTES:
        raise QuotaExceededError(
            "That picture is larger than 5 MB.", limit=MAX_UPLOAD_BYTES, used=len(data)
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in _FORMATS:
                raise InvalidRequestError("Use a PNG, JPEG or WebP picture.")
            if image.width * image.height > _MAX_PIXELS:
                raise InvalidRequestError("That picture is too large.")
            upright = ImageOps.exif_transpose(image)
            square = ImageOps.fit(
                upright.convert("RGBA"), (SIZE, SIZE), Image.Resampling.LANCZOS
            )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise InvalidRequestError("That is not a picture we can read.") from None
    out = io.BytesIO()
    square.save(out, "PNG", optimize=True)
    return out.getvalue()


class AvatarService:
    def __init__(self, files: FileStore) -> None:
        self._files = files

    async def replace(
        self, caller: Caller, workspace_id: str, previous: str | None, data: bytes
    ) -> str:
        """Store the picture made from `data` for a workspace's owner and delete the one it replaces. Returns its key."""
        png = render(data)
        key = workspace_avatar_key(
            caller.tenant_id,
            caller.user_id,
            workspace_id,
            hashlib.sha256(png).hexdigest()[:32],
            "png",
        )
        await self._files.upload(key, png, content_type="image/png")
        if previous and previous != key:
            await self._files.delete(previous)
        return key

    async def remove(self, previous: str | None) -> None:
        if previous:
            await self._files.delete(previous)

    async def read(self, key: str) -> bytes:
        return await self._files.download(key)


def avatars(container: Container) -> AvatarService:
    return AvatarService(container.file_store)
