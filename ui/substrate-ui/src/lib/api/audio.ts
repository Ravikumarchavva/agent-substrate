import { RealtimeToken, TranscribeResult, TTSVoice } from "@/types";
import {
  getDefaultVoiceForModel,
  getPreferredRealtimeModel,
  getPreferredRealtimeVoice,
  getPreferredSTTModel,
  getPreferredTTSFormat,
  getPreferredTTSModel,
  getPreferredTTSVoice,
  isVoiceCompatible,
} from "@/lib/model-preferences";

export const audioApi = {
  /** Send recorded audio blob to Whisper for transcription. */
  async transcribeAudio(blob: Blob, mimeType: string): Promise<TranscribeResult> {
    const ext = mimeType.includes("ogg") ? "ogg"
      : mimeType.includes("mp4") ? "mp4"
      : mimeType.includes("wav") ? "wav"
      : "webm";
    const form = new FormData();
    form.append("file", blob, `recording.${ext}`);
    form.append("model", getPreferredSTTModel());
    const res = await fetch("/chat/api/audio/transcribe", { method: "POST", body: form });
    if (!res.ok) throw new Error(`Transcription failed: ${res.statusText}`);
    return res.json();
  },

  /** Fetch TTS audio as a Blob, ready for playback. */
  async textToSpeech(
    text: string,
    voice?: TTSVoice,
    model?: string,
  ): Promise<Blob> {
    const preferredModel = model?.trim() || getPreferredTTSModel();
    const preferredVoice = voice
      ? (isVoiceCompatible(preferredModel, voice)
          ? voice
          : getDefaultVoiceForModel(preferredModel))
      : getPreferredTTSVoice(preferredModel);
    const res = await fetch("/chat/api/audio/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice: preferredVoice,
        model: preferredModel,
        response_format: getPreferredTTSFormat(preferredModel),
      }),
    });
    if (!res.ok) throw new Error(await ttsFailure(res));
    return res.blob();
  },

  /** Get a short-lived ephemeral Realtime session token from the backend. */
  async getRealtimeToken(): Promise<RealtimeToken> {
    const params = new URLSearchParams({
      model: getPreferredRealtimeModel(),
      voice: getPreferredRealtimeVoice(),
    });
    const res = await fetch(`/chat/api/audio/realtime-token?${params.toString()}`);
    if (!res.ok) throw new Error(`Failed to get realtime token: ${res.statusText}`);
    return res.json();
  },
};

/** The server's reason for a failed speech request (the proxy wraps FastAPI's `{detail}` in `{error}`), else the status text. */
async function ttsFailure(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: unknown };
    const inner = typeof body.error === "string" ? (JSON.parse(body.error) as { detail?: unknown }) : null;
    if (typeof inner?.detail === "string") return inner.detail;
  } catch {
    // not JSON; fall through
  }
  return `TTS failed: ${res.statusText}`;
}
