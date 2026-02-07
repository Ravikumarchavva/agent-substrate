from typing import Any, Dict, List, Union, Literal, Sequence, Tuple
from pydantic import BaseModel, ConfigDict, field_validator, model_serializer
from PIL import Image

from agent_framework.messages._types import (
    MediaType, ToolResponseContent
)
from agent_framework.messages.client_messages import (
    UserMessage, ToolCallMessage, AssistantMessage
)

def user_message_to_openai(msg: UserMessage) -> Dict[str, Any]:
    """Convert UserMessage to OpenAI chat message format."""
    content: List[Dict[str, Any]] = []
    for item in msg.content:
        if isinstance(item, str):
            content.append({"type": "input_text", "text": item})
        elif isinstance(item, Image.Image):
            import io
            import base64

            buffered = io.BytesIO()
            item.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            content.append({"type": "input_image", "image_data": img_str, "detail": "high"})
        else:
            raise ValueError("Unsupported media type in UserMessage content")
    return {
        "type": "message",
        "role": msg.role,
        "content": content,
    }

def assistant_message_to_openai(msg: AssistantMessage) -> Dict[str, Any]:
    """Convert AssistantMessage to OpenAI chat message format."""
    content: List[Dict[str, Any]] = []
    if msg.content:
        for item in msg.content:
            if isinstance(item, str):
                content.append({"type": "output_text", "text": item})
            elif isinstance(item, Image.Image):
                import io
                import base64

                buffered = io.BytesIO()
                item.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
                content.append({"type": "output_image", "image_data": img_str, "detail": "high"})
            else:
                raise ValueError("Unsupported media type in AssistantMessage content")
        return {
            "type": "message",
            "role": msg.role,
            "content": content,
        }
    if msg.tool_calls:
        for tc in msg.tool_calls:
            content.append({
                "type": "function_call",
                "name": tc.content.name,
                "call_id": tc.id,
                "arguments": tc.content.parameters
            })
        return content
    raise ValueError("AssistantMessage must have either content or tool_calls")