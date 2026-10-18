"""What a group member can see of what is shared, and share of what it made.

Both work on a member's *sandbox*: its own folder (``/workspace``) and the drives of the groups it is in (``/groups/<name>``), as ``drives.py``
names them. A picture shared in the group is opened for the model downscaled (every member that looks pays for its size); a file the member
made, at a path in its sandbox, becomes an attachment of the group.
"""

from __future__ import annotations

import asyncio
import io
import mimetypes
import posixpath
from dataclasses import dataclass
from typing import Any, Mapping

from PIL import Image, ImageOps

from substrate.types import MediaBlock
from substrate.workspace.layout import conversation_shared_key, safe_relative_path
from substrate_cloud.monolith.services.groups import files as group_files
from substrate_cloud.monolith.services.groups.drives import Drive

# The longest side a picture is sent at, and the most bytes it may then be.
MAX_SIDE = 1568
_MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Sandbox:
    """Where one member's files are: whose, its own folder, the group it is speaking in, and every drive it has."""

    tenant_id: str
    user_id: str
    home: str
    group: str
    drives: tuple[Drive, ...]


def _downscale(data: bytes, filename: str) -> tuple[bytes, str] | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            kept_jpeg = image.format == "JPEG"
            upright = ImageOps.exif_transpose(image)
            upright.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            if kept_jpeg:
                upright.convert("RGB").save(out, "JPEG", quality=85)
                media_type = "image/jpeg"
            else:
                upright.save(out, "PNG", optimize=True)
                media_type = "image/png"
    except Exception:  # noqa: BLE001 - something that is not a picture is not shown as one
        return None
    return (out.getvalue(), media_type) if len(out.getvalue()) <= _MAX_BYTES else None


async def load_media(
    store: Any, sandbox: Sandbox, attachment: Mapping[str, Any]
) -> MediaBlock | None:
    """The picture an attachment of the group is, ready for the model, or ``None`` when it is not one of the group's own files or not a picture."""
    key = str(attachment.get("key", ""))
    prefix = group_files.prefix_of(sandbox.tenant_id, sandbox.user_id, sandbox.group)
    if not key.startswith(prefix) or ".." in key.removeprefix(prefix).split("/"):
        return None
    try:
        data = await store.download(key)
    except Exception:  # noqa: BLE001 - a file that is gone is simply not shown
        return None
    made = await asyncio.to_thread(_downscale, data, str(attachment.get("name", "")))
    if made is None:
        return None
    png, media_type = made
    return MediaBlock(
        type="image",
        media_type=media_type,
        data=png,
        filename=str(attachment.get("name") or "picture"),
    )


def _locate(sandbox: Sandbox, path: str) -> tuple[str, str]:
    """``(workspace id, path inside it)`` for a path in the member's sandbox."""
    parts = path.split("/")
    if (
        not path.startswith("/")
        or ".." in parts
        or (parts[1:2] not in (["workspace"], ["groups"]))
    ):
        raise ValueError("only files under /workspace or /groups can be shared")
    if parts[1] == "workspace":
        return sandbox.home, "/".join(parts[2:])
    label = parts[2] if len(parts) > 2 else ""
    drive = next((d for d in sandbox.drives if d.label == label), None)
    if drive is None:
        raise ValueError(f"there is no such folder: /groups/{label}")
    return drive.workspace_id, "/".join(parts[3:])


async def publish(
    store: Any, reader: Any, sandbox: Sandbox, path: str
) -> dict[str, Any]:
    """Make the file at ``path`` in the member's sandbox an attachment of the group, and return its record.

    A file already in the group's drive is shared where it is. One from the member's own folder, or from another group it is in, is *copied* into
    this group's drive (``shared/``, never replacing a file) so everyone in the group can open it. Raises ``ValueError`` saying why it cannot.
    """
    workspace, relative = _locate(sandbox, path)
    try:
        relative = safe_relative_path(relative)
    except ValueError:
        raise ValueError("there is no such file") from None
    source = conversation_shared_key(
        sandbox.tenant_id, sandbox.user_id, workspace, relative
    )
    if not await store.exists(source):
        raise ValueError("there is no such file")
    data = await store.download(source)
    if len(data) > group_files.MAX_FILE_BYTES:
        raise ValueError("that file is too large to share")

    prefix = group_files.prefix_of(sandbox.tenant_id, sandbox.user_id, sandbox.group)
    mime = mimetypes.guess_type(relative)[0]
    if workspace != sandbox.group:
        name = await group_files.unique_name(
            store, f"{prefix}shared/", posixpath.basename(relative)
        )
        relative = f"shared/{name}"
        await store.upload(
            prefix + relative, data, content_type=mime or "application/octet-stream"
        )
    return {
        **group_files.record(prefix, relative, len(data), mime),
        **await group_files.read_excerpt(
            reader, data, posixpath.basename(relative), mime
        ),
        **await group_files.make_preview(store, prefix, relative, data, mime),
    }


__all__ = ["MAX_SIDE", "Sandbox", "load_media", "publish"]
