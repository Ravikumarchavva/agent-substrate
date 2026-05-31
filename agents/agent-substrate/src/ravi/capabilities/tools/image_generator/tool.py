"""ImageGeneratorTool — generate images via OpenAI DALL-E.

Wraps the OpenAI Images API to produce images from text prompts.
"""

from __future__ import annotations

from typing import Optional

from ravi.kernel.tools import ToolExecutionResult
from ravi.kernel import ImageBlock, TextBlock


class ImageGeneratorTool:
    """Generate images from text prompts using OpenAI DALL-E."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
async def execute(  # type: ignore[override]
        self,
        *,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
    ) -> ToolExecutionResult:
        if not prompt.strip():
            return ToolExecutionResult(
                content=[
                    TextBlock(text="Please provide a prompt describing the image.")
                ],
                is_error=True,
            )

        api_key = self._api_key
        if not api_key:
            import os

            api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text="Image generator not configured (no OpenAI API key)."
                    )
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
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=f"DALL-E API error ({exc.response.status_code}): {error_body}"
                    )
                ],
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"HTTP error calling DALL-E: {exc}")],
                is_error=True,
            )

        images = data.get("data", [])
        if not images:
            return ToolExecutionResult(
                content=[TextBlock(text="No image returned from DALL-E API.")],
                is_error=True,
            )

        image_url = images[0].get("url", "")
        revised_prompt = images[0].get("revised_prompt", prompt)

        return ToolExecutionResult(
            content=[
                TextBlock(text=f"Generated image for: {revised_prompt}"),
                ImageBlock(data=image_url, media_type="image/png"),
            ],
            app_data={"url": image_url, "revised_prompt": revised_prompt},
        )
