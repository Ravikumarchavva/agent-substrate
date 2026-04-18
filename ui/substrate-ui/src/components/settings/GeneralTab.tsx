"use client";

import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
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

const SELECT_CLASS =
  "w-full appearance-none rounded-xl border border-(--border) bg-background px-3 py-2.5 pr-8 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-(--accent)";

function Notice({
  tone,
  children,
}: {
  tone: "success" | "error" | "info";
  children: ReactNode;
}) {
  const toneClass =
    tone === "success"
      ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-200"
      : tone === "error"
        ? "border-rose-500/25 bg-rose-500/10 text-rose-200"
        : "border-(--border) bg-(--card) text-(--muted)";

  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${toneClass}`}>
      {children}
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {description && (
          <p className="mt-1 text-xs text-(--muted)">{description}</p>
        )}
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-(--muted)">{label}</span>
      {children}
    </label>
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
      onChange={(e) => onChange(e.target.value)}
      className={SELECT_CLASS}
    >
      {groups.map((group) => (
        <optgroup key={group.label} label={group.label}>
          {group.options.map((opt) => (
            <option key={opt.id} value={opt.id}>{opt.label}</option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

interface GeneralTabProps {
  customInstructions: string;
  setCustomInstructions: (v: string) => void;
  isSaving: boolean;
  saveError: string | null;
  setSaveError: (v: string | null) => void;
  saveSuccess: boolean;
  handleSaveInstructions: () => void;
  chatModel: string;
  setChatModel: (v: string) => void;
  sttModel: string;
  setSttModel: (v: string) => void;
  ttsModel: string;
  setTtsModel: (v: string) => void;
  ttsVoice: TTSVoice;
  setTtsVoice: (v: TTSVoice | ((prev: TTSVoice) => TTSVoice)) => void;
  ttsPlaybackRate: TTSPlaybackRate;
  setTtsPlaybackRate: (v: TTSPlaybackRate) => void;
  realtimeModel: string;
  setRealtimeModel: (v: string) => void;
  realtimeVoice: OpenAITTSVoice;
  setRealtimeVoice: (v: OpenAITTSVoice) => void;
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
  setModelPreferencesNotice: (v: SettingsNotice | null) => void;
}

export function GeneralTab({
  customInstructions,
  setCustomInstructions,
  isSaving,
  saveError,
  setSaveError,
  saveSuccess,
  handleSaveInstructions,
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
  hasUnsavedModelPreferences,
  isDefaultModelPreferences,
  handleSaveModelPreferences,
  handleResetModelPreferences,
  modelPreferencesNotice,
  setModelPreferencesNotice,
}: GeneralTabProps) {
  const clearNotice = () => setModelPreferencesNotice(null);

  return (
    <div className="space-y-8">
      <Section title="Custom instructions" description="Appended to the system prompt to shape tone and context.">
        <textarea
          value={customInstructions}
          onChange={(e) => { setCustomInstructions(e.target.value); setSaveError(null); }}
          rows={6}
          className="w-full resize-none rounded-xl border border-(--border) bg-background p-4 text-sm leading-7 outline-none transition focus:ring-2 focus:ring-(--accent)"
          placeholder="e.g. Always respond in British English. Keep answers concise."
        />
        {saveError && <Notice tone="error">{saveError}</Notice>}
        {saveSuccess && <Notice tone="success">Instructions saved.</Notice>}
        <div className="flex items-center gap-2">
          <button
            onClick={handleSaveInstructions}
            disabled={isSaving}
            className="inline-flex items-center gap-2 rounded-xl bg-(--accent) px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
          >
            {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
            {isSaving ? "Checking…" : "Save"}
          </button>
          {customInstructions && (
            <button
              onClick={() => { setCustomInstructions(""); localStorage.removeItem("system_instructions_override"); setSaveError(null); }}
              className="rounded-xl border border-(--border) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--card-hover) cursor-pointer"
            >
              Clear
            </button>
          )}
        </div>
      </Section>

      <div className="h-px bg-(--border)" />

      <Section title="Models" description="Default models for each capability.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Chat">
            <GroupedSelect value={chatModel} groups={groupedChatModels} onChange={(v) => { clearNotice(); setChatModel(v); }} />
          </Field>
          <Field label="Transcription">
            <GroupedSelect value={sttModel} groups={groupedSttModels} onChange={(v) => { clearNotice(); setSttModel(v); }} />
          </Field>
          <Field label="Speech synthesis">
            <GroupedSelect
              value={ttsModel}
              groups={groupedTtsModels}
              onChange={(v) => {
                clearNotice();
                setTtsModel(v);
                setTtsVoice((cur) => isVoiceCompatible(v, cur) ? cur : getDefaultVoiceForModel(v));
              }}
            />
          </Field>
          <Field label="Live voice model">
            <GroupedSelect value={realtimeModel} groups={groupedRealtimeModels} onChange={(v) => { clearNotice(); setRealtimeModel(v); }} />
          </Field>
        </div>
      </Section>

      <div className="h-px bg-(--border)" />

      <Section title="Voice" description="Voice and playback preferences.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Speech voice">
            <select
              value={ttsVoice}
              onChange={(e) => { clearNotice(); setTtsVoice(e.target.value as TTSVoice); }}
              className={SELECT_CLASS}
            >
              {ttsVoiceOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>{opt.label}</option>
              ))}
            </select>
          </Field>
          <Field label="Playback speed">
            <select
              value={String(ttsPlaybackRate)}
              onChange={(e) => { clearNotice(); setTtsPlaybackRate(Number.parseFloat(e.target.value) as TTSPlaybackRate); }}
              className={SELECT_CLASS}
            >
              {TTS_PLAYBACK_RATE_OPTIONS.map((opt) => (
                <option key={opt.id} value={opt.id}>{opt.label}</option>
              ))}
            </select>
          </Field>
          <Field label="Realtime voice">
            <select
              value={realtimeVoice}
              onChange={(e) => { clearNotice(); setRealtimeVoice(e.target.value as OpenAITTSVoice); }}
              className={SELECT_CLASS}
            >
              {realtimeVoiceOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>{opt.label}</option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      <div className="h-px bg-(--border)" />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-(--muted)">
          {hasUnsavedModelPreferences ? "You have unsaved changes." : "Preferences are up to date."}
        </p>
        <div className="flex gap-2">
          <button
            onClick={handleSaveModelPreferences}
            disabled={!hasUnsavedModelPreferences}
            className="rounded-xl bg-(--accent) px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
          >
            Save preferences
          </button>
          <button
            onClick={handleResetModelPreferences}
            disabled={isDefaultModelPreferences}
            className="rounded-xl border border-(--border) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--card-hover) disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
          >
            Reset defaults
          </button>
        </div>
      </div>

      {modelPreferencesNotice && (
        <Notice tone={modelPreferencesNotice.tone === "success" ? "success" : "info"}>
          {modelPreferencesNotice.message}
        </Notice>
      )}
    </div>
  );
}
