"use client";

import { Cpu, Mic, Radio, Volume2 } from "lucide-react";
import type {
  ModelOption,
  OpenAITTSVoice,
  TTSPlaybackRate,
  TTSVoice,
  VoiceOption,
} from "@/types";
import {
  TTS_PLAYBACK_RATE_OPTIONS,
  getDefaultVoiceForModel,
  isVoiceCompatible,
} from "@/lib/model-preferences";

interface SettingsNotice {
  tone: "success" | "info";
  message: string;
}

interface ModelOptionGroup {
  label: string;
  options: ModelOption[];
}

interface ModelsTabProps {
  chatModel: string;
  setChatModel: (value: string) => void;
  sttModel: string;
  setSttModel: (value: string) => void;
  ttsModel: string;
  setTtsModel: (value: string) => void;
  ttsVoice: TTSVoice;
  setTtsVoice: (value: TTSVoice | ((prev: TTSVoice) => TTSVoice)) => void;
  ttsPlaybackRate: TTSPlaybackRate;
  setTtsPlaybackRate: (value: TTSPlaybackRate) => void;
  realtimeModel: string;
  setRealtimeModel: (value: string) => void;
  realtimeVoice: OpenAITTSVoice;
  setRealtimeVoice: (value: OpenAITTSVoice) => void;
  groupedChatModels: ModelOptionGroup[];
  groupedSttModels: ModelOptionGroup[];
  groupedTtsModels: ModelOptionGroup[];
  groupedRealtimeModels: ModelOptionGroup[];
  ttsVoiceOptions: VoiceOption[];
  realtimeVoiceOptions: VoiceOption[];
  selectedChatModel: ModelOption | undefined;
  selectedSttModel: ModelOption | undefined;
  selectedTtsModel: ModelOption | undefined;
  selectedRealtimeModel: ModelOption | undefined;
  hasUnsavedModelPreferences: boolean;
  isDefaultModelPreferences: boolean;
  handleSaveModelPreferences: () => void;
  handleResetModelPreferences: () => void;
  modelPreferencesNotice: SettingsNotice | null;
  setModelPreferencesNotice: (value: SettingsNotice | null) => void;
}

function CapabilityCard({
  icon,
  title,
  description,
  activeModel,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  activeModel?: ModelOption;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-[24px] p-5" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-(--badge-bg) text-foreground">
            {icon}
          </div>
          <h3 className="mt-4 text-base font-semibold text-foreground">{title}</h3>
          <p className="mt-2 text-sm leading-6 text-(--muted)">{description}</p>
        </div>
        {activeModel && (
          <span className="rounded-full bg-(--badge-bg) px-3 py-1 text-[11px] font-medium text-(--badge-fg)">
            {activeModel.provider}
          </span>
        )}
      </div>
      <div className="mt-5 space-y-3">{children}</div>
    </div>
  );
}

function Notice({ tone, message }: SettingsNotice) {
  return (
    <div className={`rounded-[18px] px-4 py-3 text-sm ${tone === "success" ? "bg-emerald-500/10 text-emerald-500" : "bg-(--badge-bg) text-(--muted)"}`}>
      {message}
    </div>
  );
}

function GroupedSelect({
  value,
  groups,
  onChange,
}: {
  value: string;
  groups: ModelOptionGroup[];
  onChange: (value: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="w-full rounded-2xl border border-(--border) bg-background px-4 py-3 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-(--accent)"
    >
      {groups.map((group) => (
        <optgroup key={group.label} label={group.label}>
          {group.options.map((option) => (
            <option key={option.id} value={option.id}>{option.label}</option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

export function ModelsTab({
  chatModel,
  setChatModel,
  sttModel,
  setSttModel,
  ttsModel,
  setTtsModel,
  ttsVoice,
  setTtsVoice,
  ttsPlaybackRate,
  setTtsPlaybackRate,
  realtimeModel,
  setRealtimeModel,
  realtimeVoice,
  setRealtimeVoice,
  groupedChatModels,
  groupedSttModels,
  groupedTtsModels,
  groupedRealtimeModels,
  ttsVoiceOptions,
  realtimeVoiceOptions,
  selectedChatModel,
  selectedSttModel,
  selectedTtsModel,
  selectedRealtimeModel,
  hasUnsavedModelPreferences,
  isDefaultModelPreferences,
  handleSaveModelPreferences,
  handleResetModelPreferences,
  modelPreferencesNotice,
  setModelPreferencesNotice,
}: ModelsTabProps) {
  const clearNotice = () => setModelPreferencesNotice(null);
  const providerCards = [
    {
      label: "OpenAI",
      status: selectedChatModel?.provider === "openai" ? "Active" : "Available",
      detail: "Chat, transcription, voice, and realtime are supported.",
    },
    {
      label: "Google",
      status: [selectedChatModel?.provider, selectedTtsModel?.provider].includes("google") ? "Active" : "Available",
      detail: "Gemini chat and voice models with multimodal coverage.",
    },
    {
      label: "Groq",
      status: selectedChatModel?.provider === "groq" ? "Active" : "Available",
      detail: "Low-latency chat presets for quick responses.",
    },
    {
      label: "OpenRouter",
      status: selectedChatModel?.provider === "openrouter" ? "Active" : "Available",
      detail: "Free and routed models for experimentation.",
    },
    {
      label: "Anthropic",
      status: "Soon",
      detail: "Design placeholder added from the reference workspace.",
    },
    {
      label: "Ollama",
      status: "Soon",
      detail: "Local provider management can plug in here later.",
    },
  ];

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h2 className="text-3xl font-semibold tracking-tight text-foreground">LLM Setup</h2>
        <p className="max-w-3xl text-sm leading-6 text-(--muted)">
          Dedicated model and voice controls inspired by the admin screenshots, but wired to the same local preferences the chat composer already uses.
        </p>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="rounded-[26px] p-6 xl:col-span-2" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-(--muted)">Enabled provider defaults</div>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <div>
              <p className="text-xs font-medium text-(--muted)">Chat default</p>
              <p className="mt-1 text-lg font-semibold text-foreground">{selectedChatModel?.label ?? "Unset"}</p>
              <p className="mt-1 text-sm text-(--muted)">{selectedChatModel?.description}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-(--muted)">Voice default</p>
              <p className="mt-1 text-lg font-semibold text-foreground">{selectedTtsModel?.label ?? "Unset"}</p>
              <p className="mt-1 text-sm text-(--muted)">{selectedTtsModel?.description}</p>
            </div>
          </div>
        </div>

        <div className="rounded-[26px] p-6" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-(--muted)">Workspace status</div>
          <p className="mt-4 text-sm leading-6 text-(--muted)">
            {hasUnsavedModelPreferences
              ? "You have pending changes that are only local until saved."
              : "Model defaults are synced to local preferences and used immediately in chat."}
          </p>
          <div className="mt-6 flex gap-2">
            <button
              type="button"
              onClick={handleSaveModelPreferences}
              disabled={!hasUnsavedModelPreferences}
              className="rounded-xl bg-(--accent) px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"
            >
              Save defaults
            </button>
            <button
              type="button"
              onClick={handleResetModelPreferences}
              disabled={isDefaultModelPreferences}
              className="rounded-xl border border-(--border) px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-(--card-hover) disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"
            >
              Reset
            </button>
          </div>
        </div>
      </div>

      {modelPreferencesNotice && <Notice {...modelPreferencesNotice} />}

      <div className="grid gap-5 xl:grid-cols-2">
        <CapabilityCard
          icon={<Cpu className="h-5 w-5" />}
          title="Chat"
          description="Primary model used in the composer and standard message streaming."
          activeModel={selectedChatModel}
        >
          <GroupedSelect value={chatModel} groups={groupedChatModels} onChange={(value) => { clearNotice(); setChatModel(value); }} />
        </CapabilityCard>

        <CapabilityCard
          icon={<Mic className="h-5 w-5" />}
          title="Transcription"
          description="Speech-to-text model used by the recorder before a message is sent."
          activeModel={selectedSttModel}
        >
          <GroupedSelect value={sttModel} groups={groupedSttModels} onChange={(value) => { clearNotice(); setSttModel(value); }} />
        </CapabilityCard>

        <CapabilityCard
          icon={<Volume2 className="h-5 w-5" />}
          title="Speech synthesis"
          description="Narration model, playback speed, and default assistant voice."
          activeModel={selectedTtsModel}
        >
          <GroupedSelect
            value={ttsModel}
            groups={groupedTtsModels}
            onChange={(value) => {
              clearNotice();
              setTtsModel(value);
              setTtsVoice((current) => (isVoiceCompatible(value, current) ? current : getDefaultVoiceForModel(value)));
            }}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <select
              value={ttsVoice}
              onChange={(event) => { clearNotice(); setTtsVoice(event.target.value as TTSVoice); }}
              className="w-full rounded-2xl border border-(--border) bg-background px-4 py-3 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-(--accent)"
            >
              {ttsVoiceOptions.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
            <select
              value={String(ttsPlaybackRate)}
              onChange={(event) => { clearNotice(); setTtsPlaybackRate(Number.parseFloat(event.target.value) as TTSPlaybackRate); }}
              className="w-full rounded-2xl border border-(--border) bg-background px-4 py-3 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-(--accent)"
            >
              {TTS_PLAYBACK_RATE_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </div>
        </CapabilityCard>

        <CapabilityCard
          icon={<Radio className="h-5 w-5" />}
          title="Realtime voice"
          description="Default live conversation model and its voice profile."
          activeModel={selectedRealtimeModel}
        >
          <GroupedSelect value={realtimeModel} groups={groupedRealtimeModels} onChange={(value) => { clearNotice(); setRealtimeModel(value); }} />
          <select
            value={realtimeVoice}
            onChange={(event) => { clearNotice(); setRealtimeVoice(event.target.value as OpenAITTSVoice); }}
            className="w-full rounded-2xl border border-(--border) bg-background px-4 py-3 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-(--accent)"
          >
            {realtimeVoiceOptions.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </CapabilityCard>
      </div>

      <div className="space-y-4">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-[0.16em] text-(--muted)">Providers</h3>
          <p className="mt-2 text-sm leading-6 text-(--muted)">A dedicated provider grid inspired by the reference workspace. Supported providers are live; the rest are visual placeholders until backend support lands.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {providerCards.map((provider) => (
            <div key={provider.label} className="rounded-[22px] p-5" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
              <div className="flex items-center justify-between gap-3">
                <h4 className="text-base font-semibold text-foreground">{provider.label}</h4>
                <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${provider.status === "Soon" ? "bg-(--badge-bg) text-(--badge-fg)" : "bg-emerald-500/10 text-emerald-500"}`}>
                  {provider.status}
                </span>
              </div>
              <p className="mt-3 text-sm leading-6 text-(--muted)">{provider.detail}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}