from PIL import Image
from typing import Dict, Any, Union

MediaType = Union[str, Image.Image]

def serialize_media_content(content: MediaType) -> Union[str, Dict[str, Any]]:
    """Serialize media content for messages."""
    if isinstance(content, Image.Image):
        import io
        import base64

        buffered = io.BytesIO()
        content.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return {"type": "image/png", "data": img_str}
    elif isinstance(content, str):
        return content
    else:
        raise ValueError("Unsupported media content type")
    
def deserialize_media_content(data: Union[str, Dict[str, Any]]) -> MediaType:
    """Deserialize media content from messages."""
    if isinstance(data, dict) and data.get("type") == "image/png":
        import io
        import base64

        img_data = base64.b64decode(data["data"])
        image = Image.open(io.BytesIO(img_data))
        return image
    elif isinstance(data, str):
        return data
    else:
        raise ValueError("Unsupported media content format")