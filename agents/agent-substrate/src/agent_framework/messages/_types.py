from PIL import Image
from typing import Dict, Any, Union
from pathlib import Path

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

MediaType = Union[str, Image.Image, AudioContent, VideoContent]

# Streaming event types
class StreamChunk:
    """Base class for streaming chunks from LLM/Agent."""
    def __init__(self, type: str, data: Any = None, metadata: Dict[str, Any] = None):
        self.type = type
        self.data = data
        self.metadata = metadata or {}
    
    def __repr__(self):
        return f"StreamChunk(type={self.type}, data={self.data!r})"

class TextDeltaChunk(StreamChunk):
    """Incremental text content."""
    def __init__(self, text: str, metadata: Dict[str, Any] = None):
        super().__init__("text_delta", text, metadata)
        self.text = text

class ReasoningDeltaChunk(StreamChunk):
    """Incremental reasoning/thinking content (for o1/o3 models)."""
    def __init__(self, text: str, metadata: Dict[str, Any] = None):
        super().__init__("reasoning_delta", text, metadata)
        self.text = text

class CompletionChunk(StreamChunk):
    """Final completion event with full response."""
    def __init__(self, message: Any, metadata: Dict[str, Any] = None):
        super().__init__("completion", message, metadata)
        self.message = message

def serialize_media_content(content: MediaType, role: str = "user") -> Dict[str, Any]:
    """Serialize media content for messages in OpenAI format.
    
    Args:
        content: The media content to serialize
        role: The message role (affects type names for OpenAI Responses API)
    """
    if isinstance(content, Image.Image):
        import io
        import base64

        buffered = io.BytesIO()
        content.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        # Use appropriate type based on role
        img_type = "input_image" if role == "user" else "output_image"
        
        return {
            "type": img_type,
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img_str
            }
        }
    elif isinstance(content, AudioContent):
        import base64
        
        # Load audio data
        if isinstance(content.data, (str, Path)):
            with open(content.data, 'rb') as f:
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
                "data": audio_str
            }
        }
    elif isinstance(content, VideoContent):
        import base64
        
        # Load video data
        if isinstance(content.data, (str, Path)):
            with open(content.data, 'rb') as f:
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
                "data": video_str
            }
        }
    elif isinstance(content, str):
        # Use appropriate type based on role for OpenAI Responses API
        text_type = "input_text" if role == "user" else "output_text"
        return {"type": text_type, "text": content}
    else:
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
            import io
            import base64
            
            # New format with source
            if "source" in data:
                source = data["source"]
                if source.get("type") == "base64":
                    img_data = base64.b64decode(source["data"])
                    image = Image.open(io.BytesIO(img_data))
                    return image
            
            # Old format with image_url
            elif "image_url" in data:
                url = data["image_url"].get("url", "")
                if url.startswith("data:image/png;base64,"):
                    img_data = base64.b64decode(url.split(",", 1)[1])
                    image = Image.open(io.BytesIO(img_data))
                    return image
                else:
                    # Return URL as string for now
                    return url
        
        # Handle audio content
        elif content_type in ("input_audio", "output_audio"):
            import base64
            if "source" in data:
                source = data["source"]
                if source.get("type") == "base64":
                    audio_data = base64.b64decode(source["data"])
                    media_type = source.get("media_type", "audio/mp3")
                    format = media_type.split("/")[-1]
                    return AudioContent(data=audio_data, format=format)
        
        # Handle video content
        elif content_type in ("input_video", "output_video"):
            import base64
            if "source" in data:
                source = data["source"]
                if source.get("type") == "base64":
                    video_data = base64.b64decode(source["data"])
                    media_type = source.get("media_type", "video/mp4")
                    format = media_type.split("/")[-1]
                    return VideoContent(data=video_data, format=format)
        
        # Legacy format
        elif content_type == "image/png":
            import io
            import base64
            img_data = base64.b64decode(data["data"])
            image = Image.open(io.BytesIO(img_data))
            return image
    
    elif isinstance(data, str):
        return data
    
    raise ValueError(f"Unsupported media content format: {data}")