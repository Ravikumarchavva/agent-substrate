from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Dict, Any, Literal, Optional, Union

from PIL import Image


# Image content wrapper — for URL, file-ID, or raw-bytes inputs
class ImageContent:
    """Wrapper for image inputs that aren't already PIL Image objects.

    Three source types are supported, matching the OpenAI Responses API::

        ImageContent(url="https://example.com/photo.jpg")          # public URL
        ImageContent(file_id="file-abc123")                        # Files-API ID
        ImageContent(data=b"...", media_type="image/jpeg")         # raw bytes

    Optionally set ``detail`` to ``"low"``, ``"high"``, ``"original"``, or
    ``"auto"`` (default) to control tokenisation cost.
    """

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        file_id: Optional[str] = None,
        data: Optional[bytes] = None,
        media_type: str = "image/jpeg",
        detail: Literal["low", "high", "original", "auto"] = "auto",
    ):
        if sum(x is not None for x in (url, file_id, data)) != 1:
            raise ValueError("Exactly one of url, file_id, or data must be provided")
        self.url = url
        self.file_id = file_id
        self.data = data
        self.media_type = media_type
        self.detail = detail

    def __repr__(self) -> str:
        if self.url:
            return f"ImageContent(url={self.url!r}, detail={self.detail!r})"
        if self.file_id:
            return f"ImageContent(file_id={self.file_id!r}, detail={self.detail!r})"
        return (
            f"ImageContent(bytes={len(self.data or b'')} bytes, detail={self.detail!r})"
        )


# Audio content wrapper
class AudioContent:
    """Wrapper for audio data (file path or bytes)."""

    def __init__(self, data: Union[bytes, str, Path], format: str = "mp3"):
        self.data = data
        self.format = format  # mp3, wav, ogg, etc.

    def __repr__(self):
        if isinstance(self.data, (str, Path)):
            return f"AudioContent(file={self.data}, format={self.format})"
        return f"AudioContent(bytes={len(self.data)} bytes, format={self.format})"


# Video content wrapper
class VideoContent:
    """Wrapper for video data (file path or bytes)."""

    def __init__(self, data: Union[bytes, str, Path], format: str = "mp4"):
        self.data = data
        self.format = format  # mp4, webm, etc.

    def __repr__(self):
        if isinstance(self.data, (str, Path)):
            return f"VideoContent(file={self.data}, format={self.format})"
        return f"VideoContent(bytes={len(self.data)} bytes, format={self.format})"


MediaType = Union[str, Image.Image, ImageContent, AudioContent, VideoContent]


# Streaming event types
class StreamChunk:
    """Base class for streaming chunks from LLM/Agent."""

    def __init__(
        self, type: str, data: Any = None, metadata: Optional[Dict[str, Any]] = None
    ):
        self.type = type
        self.data = data
        self.metadata = metadata or {}

    def __repr__(self):
        return f"StreamChunk(type={self.type}, data={self.data!r})"


class TextDeltaChunk(StreamChunk):
    """Incremental text content."""

    def __init__(self, text: str, metadata: Optional[Dict[str, Any]] = None):
        super().__init__("text_delta", text, metadata)
        self.text = text


class ReasoningDeltaChunk(StreamChunk):
    """Incremental reasoning/thinking content (for o1/o3 models)."""

    def __init__(self, text: str, metadata: Optional[Dict[str, Any]] = None):
        super().__init__("reasoning_delta", text, metadata)
        self.text = text


class CompletionChunk(StreamChunk):
    """Final completion event with full response."""

    def __init__(self, message: Any, metadata: Optional[Dict[str, Any]] = None):
        super().__init__("completion", message, metadata)
        self.message = message


class StructuredOutputChunk(StreamChunk):
    """Yielded at the end of a streaming run when ``response_schema`` is set.

    Contains the validated Pydantic instance alongside the raw JSON text.
    Consumers can check ``chunk.result.ok`` before accessing ``chunk.result.parsed``.
    """

    def __init__(self, result: Any, metadata: Optional[Dict[str, Any]] = None):
        super().__init__("structured_output", result, metadata)
        self.result = result


def _pil_to_data_url(image: Image.Image) -> str:
    """Encode a PIL Image as a PNG data URL (base64)."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def serialize_media_content(content: MediaType, role: str = "user") -> Dict[str, Any]:
    """Serialize media content for messages in OpenAI Responses API format.

    Args:
        content: The media content to serialize
        role: The message role (affects type names for OpenAI Responses API)
    """
    if isinstance(content, Image.Image):
        # PIL Image → base64 PNG data URL (Responses API ``input_image``)
        return {
            "type": "input_image",
            "image_url": _pil_to_data_url(content),
        }

    if isinstance(content, ImageContent):
        block: Dict[str, Any] = {"type": "input_image"}
        if content.url:
            block["image_url"] = content.url
        elif content.file_id:
            block["file_id"] = content.file_id
        else:  # raw bytes
            b64 = base64.b64encode(content.data or b"").decode("utf-8")
            block["image_url"] = f"data:{content.media_type};base64,{b64}"
        if content.detail != "auto":
            block["detail"] = content.detail
        return block
    if isinstance(content, AudioContent):
        # Load audio data
        if isinstance(content.data, (str, Path)):
            with open(content.data, "rb") as f:
                audio_bytes = f.read()
        else:
            audio_bytes = content.data

        audio_str = base64.b64encode(audio_bytes).decode("utf-8")
        audio_type = "input_audio" if role == "user" else "output_audio"

        return {
            "type": audio_type,
            "source": {
                "type": "base64",
                "media_type": f"audio/{content.format}",
                "data": audio_str,
            },
        }
    if isinstance(content, VideoContent):
        # Load video data
        if isinstance(content.data, (str, Path)):
            with open(content.data, "rb") as f:
                video_bytes = f.read()
        else:
            video_bytes = content.data

        video_str = base64.b64encode(video_bytes).decode("utf-8")
        video_type = "input_video" if role == "user" else "output_video"

        return {
            "type": video_type,
            "source": {
                "type": "base64",
                "media_type": f"video/{content.format}",
                "data": video_str,
            },
        }
    if isinstance(content, str):
        # Use appropriate type based on role for OpenAI Responses API
        text_type = "input_text" if role == "user" else "output_text"
        return {"type": text_type, "text": content}
    raise ValueError(f"Unsupported media content type: {type(content)}")


def deserialize_media_content(data: Union[str, Dict[str, Any]]) -> MediaType:
    """Deserialize media content from messages."""
    if isinstance(data, dict):
        content_type = data.get("type", "")

        # Handle text content (both old and new formats)
        if content_type in ("text", "input_text", "output_text", "summary_text"):
            return data.get("text", "")

        # Handle image content
        elif content_type in ("image_url", "input_image", "output_image"):
            # URL or file_id variant → reconstruct as ImageContent
            if "file_id" in data:
                return ImageContent(
                    file_id=data["file_id"], detail=data.get("detail", "auto")
                )  # type: ignore[arg-type]

            image_url = data.get("image_url", "")
            if isinstance(image_url, dict):  # Chat-Completions format
                image_url = image_url.get("url", "")

            if image_url.startswith("data:"):
                # data URL — decode to PIL Image
                header, encoded = image_url.split(",", 1)
                img_data = base64.b64decode(encoded)
                return Image.open(io.BytesIO(img_data))

            if image_url:
                # Regular URL — preserve as ImageContent so callers can pass it back
                return ImageContent(url=image_url, detail=data.get("detail", "auto"))  # type: ignore[arg-type]

            # Old Anthropic-style ``source`` block
            if "source" in data:
                source = data["source"]
                if source.get("type") == "base64":
                    img_data = base64.b64decode(source["data"])
                    return Image.open(io.BytesIO(img_data))

        # Handle audio content
        elif content_type in ("input_audio", "output_audio"):
            if "source" in data:
                source = data["source"]
                if source.get("type") == "base64":
                    audio_data = base64.b64decode(source["data"])
                    media_type = source.get("media_type", "audio/mp3")
                    fmt = media_type.split("/")[-1]
                    return AudioContent(data=audio_data, format=fmt)

        # Handle video content
        elif content_type in ("input_video", "output_video"):
            if "source" in data:
                source = data["source"]
                if source.get("type") == "base64":
                    video_data = base64.b64decode(source["data"])
                    media_type = source.get("media_type", "video/mp4")
                    fmt = media_type.split("/")[-1]
                    return VideoContent(data=video_data, format=fmt)

        # Legacy format
        elif content_type == "image/png":
            img_data = base64.b64decode(data["data"])
            return Image.open(io.BytesIO(img_data))

    elif isinstance(data, str):
        return data

    raise ValueError(f"Unsupported media content format: {data}")
