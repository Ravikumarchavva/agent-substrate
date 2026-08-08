import type {
  ModelOption,
  ModelProvider,
  OpenAITTSVoice,
  TTSPlaybackRate,
  TTSVoice,
  VoiceOption,
} from "@/types";

export const CHAT_MODEL_STORAGE_KEY = "chat_model";
export const STT_MODEL_STORAGE_KEY = "stt_model";
export const TTS_MODEL_STORAGE_KEY = "tts_model";
export const TTS_VOICE_STORAGE_KEY = "tts_voice";
export const TTS_PLAYBACK_RATE_STORAGE_KEY = "tts_playback_rate";
export const REALTIME_MODEL_STORAGE_KEY = "realtime_model";
export const REALTIME_VOICE_STORAGE_KEY = "realtime_voice";
export const MODEL_PREFERENCES_UPDATED_EVENT = "substrate:model-preferences-updated";

export const DEFAULT_CHAT_MODEL = "google/gemini-3.1-flash-lite";
export const DEFAULT_STT_MODEL = "openai/gpt-4o-mini-transcribe";
export const DEFAULT_TTS_MODEL = "google/gemini-3.1-flash-tts-preview";
export const DEFAULT_TTS_VOICE: TTSVoice = "Kore";
export const DEFAULT_TTS_PLAYBACK_RATE: TTSPlaybackRate = 2;
export const DEFAULT_REALTIME_MODEL = "openai/gpt-4o-realtime-preview-2024-12-17";
export const DEFAULT_REALTIME_VOICE: OpenAITTSVoice = "coral";

const TTS_PLAYBACK_RATE_VALUES: readonly TTSPlaybackRate[] = [0.75, 1, 1.25, 1.5, 1.75, 2];

const PROVIDER_LABELS: Record<ModelProvider, string> = {
  openai: "OpenAI",
  groq: "Groq",
  google: "Google",
  openrouter: "OpenRouter Free",
  nvidia: "NVIDIA NIM",
};

export const CHAT_MODEL_OPTIONS: ModelOption[] = [
  {
    id: "google/gemini-3.1-flash-lite",
    label: "Gemini 3.1 Flash Lite",
    provider: "google",
    description: "Default — fast and cost-efficient Gemini model.",
    thinkingLevels: ["off"],
  },
  {
    id: "openai/gpt-5.4-mini",
    label: "GPT-5.4 Mini",
    provider: "openai",
    description: "High-quality OpenAI default.",
    thinkingLevels: ["off", "low", "medium", "high", "xhigh"],
  },
  {
    id: "openai/gpt-5.4",
    label: "GPT-5.4",
    provider: "openai",
    description: "Full-size OpenAI model — higher quality than Mini, more expensive.",
    thinkingLevels: ["off", "low", "medium", "high", "xhigh"],
  },
  {
    id: "nvidia/moonshotai/kimi-k2.6",
    label: "Kimi K2.6",
    provider: "nvidia",
    description: "Multimodal Kimi K2.6 by Moonshot AI via NVIDIA NIM.",
    thinkingLevels: ["off"],
  },
  {
    id: "openrouter/qwen/qwen3-coder:free",
    label: "Qwen3 Coder",
    provider: "openrouter",
    description: "Best free coding-oriented model.",
    thinkingLevels: ["off", "low", "medium"],
  },
  {
    id: "groq/llama-3.3-70b-versatile",
    label: "Llama 3.3 70B",
    provider: "groq",
    description: "Powerful Groq-hosted Llama model.",
    thinkingLevels: ["off", "low", "medium"],
  },
];

export const STT_MODEL_OPTIONS: ModelOption[] = [
  {
    id: "openai/gpt-4o-mini-transcribe",
    label: "GPT-4o Mini Transcribe",
    provider: "openai",
    description: "Recommended low-cost transcription model.",
  },
  {
    id: "openai/gpt-4o-transcribe",
    label: "GPT-4o Transcribe",
    provider: "openai",
    description: "Higher-quality OpenAI speech-to-text option.",
  },
  {
    id: "openai/whisper-1",
    label: "Whisper-1",
    provider: "openai",
    description: "Legacy Whisper transcription model.",
  },
];

export const TTS_MODEL_OPTIONS: ModelOption[] = [
  {
    id: "google/gemini-3.1-flash-tts-preview",
    label: "Gemini 3.1 Flash TTS",
    provider: "google",
    description: "Best low-latency Google TTS option and the default speech model.",
  },
  {
    id: "google/gemini-2.5-flash-preview-tts",
    label: "Gemini 2.5 Flash TTS",
    provider: "google",
    description: "Fast preview TTS model for cost-efficient speech playback.",
  },
  {
    id: "google/gemini-2.5-pro-preview-tts",
    label: "Gemini 2.5 Pro TTS",
    provider: "google",
    description: "Higher-fidelity Gemini speech for longer narration.",
  },
  {
    id: "openai/gpt-4o-mini-tts",
    label: "GPT-4o Mini TTS",
    provider: "openai",
    description: "OpenAI speech model with style instructions support.",
  },
  {
    id: "openai/tts-1",
    label: "TTS-1",
    provider: "openai",
    description: "Legacy fast OpenAI TTS model.",
  },
  {
    id: "openai/tts-1-hd",
    label: "TTS-1 HD",
    provider: "openai",
    description: "Legacy higher-quality OpenAI TTS output.",
  },
];

export const REALTIME_MODEL_OPTIONS: ModelOption[] = [
  {
    id: "openai/gpt-4o-realtime-preview-2024-12-17",
    label: "GPT-4o Realtime Preview",
    provider: "openai",
    description: "Current backend-supported live voice model.",
  },
];

export const TTS_VOICE_OPTIONS: VoiceOption[] = [
  { id: "alloy", label: "Alloy", provider: "openai", description: "Neutral synthetic voice." },
  { id: "ash", label: "Ash", provider: "openai", description: "Calm, grounded delivery." },
  { id: "ballad", label: "Ballad", provider: "openai", description: "Softer, warmer speech." },
  { id: "coral", label: "Coral", provider: "openai", description: "Default OpenAI realtime voice." },
  { id: "echo", label: "Echo", provider: "openai", description: "Clear, bright tone." },
  { id: "fable", label: "Fable", provider: "openai", description: "Narration-friendly voice." },
  { id: "nova", label: "Nova", provider: "openai", description: "Upbeat and energetic." },
  { id: "onyx", label: "Onyx", provider: "openai", description: "Deeper and steadier voice." },
  { id: "sage", label: "Sage", provider: "openai", description: "Measured, assistant-like voice." },
  { id: "shimmer", label: "Shimmer", provider: "openai", description: "Lighter, airy delivery." },
  { id: "verse", label: "Verse", provider: "openai", description: "Expressive voice for reading." },
  { id: "marin", label: "Marin", provider: "openai", description: "Smooth conversational tone." },
  { id: "cedar", label: "Cedar", provider: "openai", description: "Firm, lower-register voice." },
  { id: "Kore", label: "Kore", provider: "google", description: "Default Gemini TTS voice." },
  { id: "Puck", label: "Puck", provider: "google", description: "Brisk and playful." },
  { id: "Aoede", label: "Aoede", provider: "google", description: "Warm and musical." },
  { id: "Zephyr", label: "Zephyr", provider: "google", description: "Lighter, faster speech." },
  { id: "Charon", label: "Charon", provider: "google", description: "Deeper and more formal." },
  { id: "Fenrir", label: "Fenrir", provider: "google", description: "Confident, punchier delivery." },
  { id: "Sulafat", label: "Sulafat", provider: "google", description: "Smoother narration tone." },
  { id: "Achernar", label: "Achernar", provider: "google", description: "Bright and conversational." },
];

export const TTS_PLAYBACK_RATE_OPTIONS: Array<{
  id: TTSPlaybackRate;
  label: string;
  description: string;
}> = [
  { id: 0.75, label: "0.75x", description: "Slower delivery for careful listening." },
  { id: 1, label: "1x", description: "Normal speech playback speed." },
  { id: 1.25, label: "1.25x", description: "Slightly faster playback." },
  { id: 1.5, label: "1.5x", description: "Fast playback for most responses." },
  { id: 1.75, label: "1.75x", description: "Very fast playback." },
  { id: 2, label: "2x", description: "Default accelerated playback for assistant speech." },
];

function readStoredValue(storageKey: string, fallbackValue: string): string {
  if (typeof window === "undefined") return fallbackValue;
  const storedValue = window.localStorage.getItem(storageKey)?.trim();
  return storedValue || fallbackValue;
}

function emitPreferenceChange(storageKey: string, value: string | null): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(MODEL_PREFERENCES_UPDATED_EVENT, {
      detail: { key: storageKey, value },
    }),
  );
}

export function writeStoredValue(storageKey: string, value: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey, value.trim());
  emitPreferenceChange(storageKey, value.trim());
}

export function clearStoredValue(storageKey: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(storageKey);
  emitPreferenceChange(storageKey, null);
}

function isPlaybackRate(value: number): value is TTSPlaybackRate {
  return TTS_PLAYBACK_RATE_VALUES.includes(value as TTSPlaybackRate);
}

export function getProviderForModel(model: string): ModelProvider {
  const normalizedModel = model.trim().toLowerCase();
  if (normalizedModel.startsWith("openrouter/")) return "openrouter";
  if (normalizedModel.startsWith("groq/")) return "groq";
  if (normalizedModel.startsWith("google/") || normalizedModel.startsWith("gemini/")) {
    return "google";
  }
  return "openai";
}

export function groupModelOptions(options: ModelOption[]): Array<{
  label: string;
  options: ModelOption[];
}> {
  const groups = new Map<string, ModelOption[]>();
  for (const option of options) {
    const label = PROVIDER_LABELS[option.provider];
    const existing = groups.get(label) ?? [];
    existing.push(option);
    groups.set(label, existing);
  }
  return Array.from(groups.entries()).map(([label, groupedOptions]) => ({
    label,
    options: groupedOptions,
  }));
}

export function getPreferredChatModel(): string {
  return readStoredValue(CHAT_MODEL_STORAGE_KEY, DEFAULT_CHAT_MODEL);
}

export function getPreferredSTTModel(): string {
  return readStoredValue(STT_MODEL_STORAGE_KEY, DEFAULT_STT_MODEL);
}

export function getPreferredTTSModel(): string {
  return readStoredValue(TTS_MODEL_STORAGE_KEY, DEFAULT_TTS_MODEL);
}

export function getPreferredRealtimeModel(): string {
  return readStoredValue(REALTIME_MODEL_STORAGE_KEY, DEFAULT_REALTIME_MODEL);
}

export function getVoiceOptionsForModel(model: string): VoiceOption[] {
  const provider = getProviderForModel(model);
  const voiceProvider = provider === "google" ? "google" : "openai";
  return TTS_VOICE_OPTIONS.filter((option) => option.provider === voiceProvider);
}

export function isVoiceCompatible(model: string, voice: string): voice is TTSVoice {
  return getVoiceOptionsForModel(model).some((option) => option.id === voice);
}

export function getDefaultVoiceForModel(model: string): TTSVoice {
  return getProviderForModel(model) === "google" ? DEFAULT_TTS_VOICE : DEFAULT_REALTIME_VOICE;
}

export function getPreferredTTSVoice(model = getPreferredTTSModel()): TTSVoice {
  const storedVoice = readStoredValue(TTS_VOICE_STORAGE_KEY, "");
  return isVoiceCompatible(model, storedVoice)
    ? storedVoice
    : getDefaultVoiceForModel(model);
}

export function getPreferredTTSPlaybackRate(): TTSPlaybackRate {
  const storedRate = Number.parseFloat(
    readStoredValue(TTS_PLAYBACK_RATE_STORAGE_KEY, String(DEFAULT_TTS_PLAYBACK_RATE)),
  );
  return isPlaybackRate(storedRate) ? storedRate : DEFAULT_TTS_PLAYBACK_RATE;
}

export function formatPlaybackRateLabel(rate: TTSPlaybackRate): string {
  return Number.isInteger(rate) ? `${rate.toFixed(0)}x` : `${rate}x`;
}

export function getPreferredRealtimeVoice(): OpenAITTSVoice {
  const storedVoice = readStoredValue(REALTIME_VOICE_STORAGE_KEY, "");
  return getVoiceOptionsForModel(DEFAULT_REALTIME_MODEL).some(
    (option) => option.id === storedVoice
  )
    ? (storedVoice as OpenAITTSVoice)
    : DEFAULT_REALTIME_VOICE;
}

export function getPreferredTTSFormat(model: string): "mp3" | "wav" {
  return getProviderForModel(model) === "google" ? "wav" : "mp3";
}