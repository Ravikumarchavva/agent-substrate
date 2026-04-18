/** Available TTS voices from the OpenAI TTS API. */
export type OpenAITTSVoice =
  | "alloy" | "ash" | "ballad" | "coral" | "echo"
  | "fable" | "nova" | "onyx" | "sage" | "shimmer"
  | "verse" | "marin" | "cedar";

/** Curated Gemini TTS preview voices. */
export type GoogleTTSVoice =
  | "Kore"
  | "Puck"
  | "Aoede"
  | "Zephyr"
  | "Charon"
  | "Fenrir"
  | "Sulafat"
  | "Achernar";

export type TTSVoice = OpenAITTSVoice | GoogleTTSVoice;

/** Supported playback-speed presets for generated assistant speech. */
export type TTSPlaybackRate = 0.75 | 1 | 1.25 | 1.5 | 1.75 | 2;

/** Response from POST /api/audio/transcribe */
export type TranscribeResult = {
  text: string;
};

/** Response from GET /api/audio/realtime-token */
export type RealtimeToken = {
  client_secret: string;
  expires_at: number;
  session_id: string;
};
