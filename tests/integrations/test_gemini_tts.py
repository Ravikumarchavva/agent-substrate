"""Gemini TTS: raw PCM from some models is made playable, and the text is sent as it is."""

from __future__ import annotations

import io
import wave
from types import SimpleNamespace

from substrate.integrations.llm.gemini.gemini_client import GeminiClient, _as_wav


def test_raw_pcm_gets_a_wav_header_with_the_rate_from_the_mime_type():
    out = _as_wav(b"\x01\x00" * 100, "audio/L16;codec=pcm;rate=24000")
    with wave.open(io.BytesIO(out)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getnframes()) == (
            24000,
            1,
            100,
        )


def test_wav_is_passed_through_untouched():
    wav = b"RIFF" + b"\x00" * 40
    assert _as_wav(wav, "audio/wav") is wav


async def test_text_is_sent_bare_and_a_style_instruction_goes_in_front():
    sent: list[str] = []

    async def generate_content(*, model, contents, config):
        sent.append(contents[0].parts[0].text)
        part = SimpleNamespace(
            inline_data=SimpleNamespace(data=b"RIFFxxxx", mime_type="audio/wav")
        )
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))]
        )

    client = GeminiClient.__new__(GeminiClient)
    client.model = "gemini-x"
    client.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    [_ async for _ in client.stream_tts(text="Rs 67 crore")]
    [_ async for _ in client.stream_tts(text="Rs 67 crore", instructions="Say calmly")]
    assert sent == ["Rs 67 crore", "Say calmly: Rs 67 crore"]
