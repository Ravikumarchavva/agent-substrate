# Multimodal Output Support - Usage Examples

## Text-only agents (existing, backward compatible)
```python
# For text models (GPT-4, Claude, etc.)
result = await agent.run("Find top 3 GitHub repos")
print(result.output_text)  # "Here are the top 3..."
# result.output = ["Here are the top 3..."]
```

## Image generation agents (DALL-E, Stable Diffusion)
```python
from agent_framework.messages import AudioContent, VideoContent
from PIL import Image

# Hypothetical image generation agent
image_agent = ImageGenAgent(
    model_client=dalle_client,
    ...
)

result = await image_agent.run("Generate a sunset over mountains")
# result.output = [Image.Image object]
# result.has_media = True
# result.media_types = ["image"]

# Access the image
for item in result.output:
    if isinstance(item, Image.Image):
        item.show()  # Display
        item.save("sunset.png")  # Save

print(result.output_text)  # "[Image: 1024x1024]"
```

## Audio conversation agents (Voice assistants)
```python
# Hypothetical voice agent
voice_agent = VoiceAgent(
    model_client=whisper_client,
    ...
)

result = await voice_agent.run("Tell me a joke in audio")
# result.output = [AudioContent(data=bytes, format="mp3")]
# result.media_types = ["audio"]

# Save the audio
for item in result.output:
    if isinstance(item, AudioContent):
        with open("joke.mp3", "wb") as f:
            f.write(item.data)

print(result.output_text)  # "[Audio: mp3]"
```

## Mixed multimodal agents
```python
# Agent that generates text + image + audio
multimodal_agent = MultimodalAgent(...)

result = await multimodal_agent.run("Create a product demo")
# result.output = [
#     "Here's your demo:",
#     Image.Image(...),  # Product screenshot
#     AudioContent(...),  # Voiceover
# ]
# result.media_types = ["text", "image", "audio"]

# Process each type
for item in result.output:
    if isinstance(item, str):
        print(item)
    elif isinstance(item, Image.Image):
        item.save("product.png")
    elif isinstance(item, AudioContent):
        with open(f"voiceover.{item.format}", "wb") as f:
            f.write(item.data)
```

## Accessing media in steps
```python
result = await agent.run("...")

for step in result.steps:
    print(f"Step {step.step}:")
    if step.thought:
        for item in step.thought:
            if isinstance(item, str):
                print(f"  Text: {item[:50]}...")
            elif isinstance(item, Image.Image):
                print(f"  Image: {item.size}")
                item.show()
    
    # step.thought_text extracts plain text automatically
    print(f"  Summary: {step.thought_text}")
```

## Serialization (for persistence/APIs)
```python
result = await agent.run("...")

# Full export (images/audio are base64-encoded)
json_data = result.to_dict()

# Check what types are present
if result.has_media:
    print(f"Contains: {result.media_types}")
```

## Key Features

### 1. **Backward Compatible**
- Existing text-only code still works
- `result.output_text` extracts text from any format
- Text-only results: `result.output = ["text string"]`

### 2. **Type Safety**
```python
MediaType = Union[str, Image.Image, AudioContent, VideoContent]
```

### 3. **Convenience Properties**
- `result.output_text` — plain text extraction
- `result.has_media` — check for non-text content
- `result.media_types` — list of types present
- `step.thought_text` — text from multimodal thought

### 4. **Serialization Ready**
- Images → base64-encoded PNG
- Audio → base64-encoded bytes
- Video → base64-encoded bytes
- All wrapped in MCP-compatible content format

### 5. **File or Bytes**
```python
# Audio from file
AudioContent(data="path/to/audio.mp3", format="mp3")

# Audio from bytes
AudioContent(data=audio_bytes, format="mp3")

# Same for video
VideoContent(data="path/to/video.mp4", format="mp4")
VideoContent(data=video_bytes, format="mp4")
```
