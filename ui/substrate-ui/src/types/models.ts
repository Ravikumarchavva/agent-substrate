import type { TTSVoice } from "./audio";

export type ModelProvider = "openai" | "google" | "local" | "openrouter" | "groq" | "nvidia";

export type ModelOption = {
  id: string;
  label: string;
  provider: ModelProvider;
  description: string;
  disabled?: boolean;
  thinkingLevels?: string[];
};

export type VoiceOption = {
  id: TTSVoice;
  label: string;
  provider: "openai" | "google" | "local";
  description: string;
};
