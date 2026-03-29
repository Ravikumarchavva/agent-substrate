"""ImageGeneratorTool — generate images via OpenAI DALL-E.

Wraps the OpenAI Images API to produce images from text prompts.
"""

from __future__ import annotations

from typing import Optional

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk


class ImageGeneratorTool(BaseTool):
    """Generate images from text prompts using OpenAI DALL-E."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
        super().__init__(
            name="image_generator",
            description=(
                "Generate an image from a text description. "
                "Returns a URL to the generated image."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the image to generate",
                    },
                    "size": {
                        "type": "string",
                        "enum": ["1024x1024", "1024x1792", "1792x1024"],
                        "description": "Image dimensions (default: 1024x1024)",
                    },
                    "quality": {
                        "type": "string",
                        "enum": ["standard", "hd"],
                        "description": "Image quality: standard or hd (default: standard)",
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SENSITIVE,
            category="creative",
            tags=["image", "picture", "dall-e", "art", "draw", "generate", "visual"],
            aliases=["dall_e", "create_image", "generate_image"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
    ) -> ToolResult:
        if not prompt.strip():
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Please provide a prompt describing the image.",
                    }
                ],
                is_error=True,
            )

        api_key = self._api_key
        if not api_key:
            import os

            api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Image generator not configured (no OpenAI API key).",
                    }
                ],
                is_error=True,
            )

        import httpx

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "dall-e-3",
                        "prompt": prompt,
                        "n": 1,
                        "size": size,
                        "quality": quality,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            error_body = exc.response.text[:500]
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": f"DALL-E API error ({exc.response.status_code}): {error_body}",
                    }
                ],
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                content=[{"type": "text", "text": f"HTTP error calling DALL-E: {exc}"}],
                is_error=True,
            )

        images = data.get("data", [])
        if not images:
            return ToolResult(
                content=[
                    {"type": "text", "text": "No image returned from DALL-E API."}
                ],
                is_error=True,
            )

        image_url = images[0].get("url", "")
        revised_prompt = images[0].get("revised_prompt", prompt)

        return ToolResult(
            content=[
                {"type": "text", "text": f"Generated image for: {revised_prompt}"},
                {"type": "image", "url": image_url},
            ],
            app_data={"url": image_url, "revised_prompt": revised_prompt},
        )
