"use client";

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
import { Button, Page, Select, SettingGroup, SettingRow, cn } from "@/design";

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

const SELECT = "w-64 max-w-full";

function Notice({ tone, message }: SettingsNotice) {
  return <div className={cn("rounded-lg px-4 py-3 text-sm", tone === "success" ? "bg-success/10 text-success" : "bg-badge text-muted")}>{message}</div>;
}

function GroupedSelect({ value, groups, label, onChange }: { value: string; groups: ModelOptionGroup[]; label: string; onChange: (value: string) => void }) {
  return (
    <Select
      className={SELECT}
      aria-label={label}
      value={value}
      onValueChange={onChange}
      groups={groups.map((g) => ({ label: g.label, options: g.options.map((o) => ({ value: o.id, label: o.label })) }))}
    />
  );
}

function ListSelect({ value, options, label, onChange }: { value: string; options: { id: string; label: string }[]; label: string; onChange: (value: string) => void }) {
  return <Select className={SELECT} aria-label={label} value={value} onValueChange={onChange} options={options.map((o) => ({ value: o.id, label: o.label }))} />;
}

/** Which models the assistant uses: one for text, and the ones for speaking and listening. Changes take effect in chat once saved. */
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
  hasUnsavedModelPreferences,
  isDefaultModelPreferences,
  handleSaveModelPreferences,
  handleResetModelPreferences,
  modelPreferencesNotice,
  setModelPreferencesNotice,
}: ModelsTabProps) {
  const change = <T,>(set: (value: T) => void) => (value: T) => {
    setModelPreferencesNotice(null);
    set(value);
  };

  return (
    <Page
      title="LLM Setup"
      subtitle="Which models the assistant uses to write, listen and speak."
      layout="columns"
      actions={
        <>
          <Button onClick={handleResetModelPreferences} disabled={isDefaultModelPreferences}>
            Reset
          </Button>
          <Button variant="primary" onClick={handleSaveModelPreferences} disabled={!hasUnsavedModelPreferences}>
            Save
          </Button>
        </>
      }
    >
      {modelPreferencesNotice && (
        <div data-span="all">
          <Notice {...modelPreferencesNotice} />
        </div>
      )}

      <SettingGroup title="Text">
        <SettingRow label="Chat model" description="Answers your messages in the composer.">
          <GroupedSelect label="Chat model" value={chatModel} groups={groupedChatModels} onChange={change(setChatModel)} />
        </SettingRow>
      </SettingGroup>

      <SettingGroup title="Voice">
        <SettingRow label="Transcription" description="Turns what you say into text before it is sent.">
          <GroupedSelect label="Transcription model" value={sttModel} groups={groupedSttModels} onChange={change(setSttModel)} />
        </SettingRow>
        <SettingRow label="Read aloud" description="The model that reads answers to you.">
          <GroupedSelect
            label="Read aloud model"
            value={ttsModel}
            groups={groupedTtsModels}
            onChange={(value) => {
              setModelPreferencesNotice(null);
              setTtsModel(value);
              setTtsVoice((current) => (isVoiceCompatible(value, current) ? current : getDefaultVoiceForModel(value)));
            }}
          />
        </SettingRow>
        <SettingRow label="Voice" description="How the assistant sounds when it reads aloud.">
          <ListSelect label="Voice" value={ttsVoice} options={ttsVoiceOptions} onChange={change((v: string) => setTtsVoice(v as TTSVoice))} />
        </SettingRow>
        <SettingRow label="Speed" description="How fast it reads.">
          <ListSelect
            label="Speed"
            value={String(ttsPlaybackRate)}
            options={TTS_PLAYBACK_RATE_OPTIONS.map((opt) => ({ ...opt, id: String(opt.id) }))}
            onChange={change((v: string) => setTtsPlaybackRate(Number.parseFloat(v) as TTSPlaybackRate))}
          />
        </SettingRow>
        <SettingRow label="Live conversation" description="The model for talking back and forth in real time.">
          <GroupedSelect label="Live conversation model" value={realtimeModel} groups={groupedRealtimeModels} onChange={change(setRealtimeModel)} />
        </SettingRow>
        <SettingRow label="Live voice" description="The voice it uses in a live conversation.">
          <ListSelect label="Live voice" value={realtimeVoice} options={realtimeVoiceOptions} onChange={change((v: string) => setRealtimeVoice(v as OpenAITTSVoice))} />
        </SettingRow>
      </SettingGroup>
    </Page>
  );
}
