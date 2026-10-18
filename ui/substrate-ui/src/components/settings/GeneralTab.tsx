"use client";

import { useState } from "react";
import { Archive, Download, HardDrive, Settings } from "lucide-react";
import { Button, Combobox, Page, Segmented, SettingGroup, SettingRow } from "@/design";
import { useTheme, type ThemePreference } from "@/contexts/ThemeContext";
import { api } from "@/lib/api";
import { readChatWidth, readMotion, setChatWidth, setMotion, type ChatWidth, type Motion } from "@/lib/appearance";
import { schedulePreferencesPush } from "@/lib/preferences-sync";
import { reportError } from "@/lib/report-error";
import type { SettingsTab } from "./SettingsPanel";

// Common zones first (some engines list India as Asia/Calcutta, so "Kolkata" would not be found), then every IANA zone the browser knows.
const COMMON_TIMEZONES = ["Asia/Kolkata", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Sao_Paulo", "Europe/London", "Europe/Paris", "Europe/Berlin", "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney", "UTC"];
const TIMEZONES = [...new Set([...COMMON_TIMEZONES, ...Intl.supportedValuesOf("timeZone")])];

export const TIMEZONE_KEY = "user_timezone";

const isMac = typeof navigator !== "undefined" && /mac/i.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";

const SHORTCUTS: [string, string][] = [
  ["New chat", `${MOD} + Shift + O`],
  ["Search conversations", `${MOD} + K`],
  ["Jump to the message box", "/"],
  ["Send the message", "Enter"],
  ["New line in the message", "Shift + Enter"],
];

/** App-wide options: how it looks, what time it is for you, the shortcuts, and your data. What the assistant knows about you is in Personalization. */
export function GeneralTab({ onOpenTab }: { onOpenTab?: (tab: SettingsTab) => void }) {
  const { preference, setPreference } = useTheme();
  const [width, setWidthState] = useState<ChatWidth>(readChatWidth);
  const [motion, setMotionState] = useState<Motion>(readMotion);
  const [timezone, setTimezone] = useState(() => (typeof window === "undefined" ? "" : (localStorage.getItem(TIMEZONE_KEY) ?? "")));
  const [downloading, setDownloading] = useState(false);

  const changeTimezone = (value: string) => {
    setTimezone(value);
    if (value.trim()) localStorage.setItem(TIMEZONE_KEY, value.trim());
    else localStorage.removeItem(TIMEZONE_KEY);
    schedulePreferencesPush();
  };

  const download = async () => {
    setDownloading(true);
    try {
      await api.downloadMyData();
    } catch (err) {
      reportError("Couldn't export your data", err);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Page layout="columns" title="General" subtitle="How the app looks and behaves for you." icon={Settings}>
      <SettingGroup title="Appearance">
        <SettingRow label="Theme" description="Match your system, or pick one.">
          <Segmented<ThemePreference>
            label="Theme"
            value={preference}
            onChange={setPreference}
            options={[
              { value: "system", label: "System" },
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
            ]}
          />
        </SettingRow>
        <SettingRow label="Chat width" description="How wide the conversation and the message box are.">
          <Segmented<ChatWidth>
            label="Chat width"
            value={width}
            onChange={(v) => {
              setWidthState(v);
              setChatWidth(v);
              schedulePreferencesPush();
            }}
            options={[
              { value: "narrow", label: "Narrow" },
              { value: "medium", label: "Medium" },
              { value: "wide", label: "Wide" },
            ]}
          />
        </SettingRow>
        <SettingRow label="Motion" description="Reduce animation and smooth scrolling.">
          <Segmented<Motion>
            label="Motion"
            value={motion}
            onChange={(v) => {
              setMotionState(v);
              setMotion(v);
              schedulePreferencesPush();
            }}
            options={[
              { value: "system", label: "System" },
              { value: "reduced", label: "Reduced" },
            ]}
          />
        </SettingRow>
      </SettingGroup>

      <SettingGroup title="Time">
        <SettingRow label="Timezone" description="Calendar events and “every morning” schedules use it. Pick a city, for example Asia/Kolkata.">
          <div className="w-64">
            <Combobox value={timezone} onValueChange={changeTimezone} placeholder="Choose a timezone" options={TIMEZONES} />
          </div>
        </SettingRow>
      </SettingGroup>

      <SettingGroup title="Keyboard shortcuts">
        {SHORTCUTS.map(([action, keys]) => (
          <SettingRow key={action} label={action}>
            <kbd className="rounded-md border border-border bg-background px-2 py-1 font-mono text-2xs text-muted">{keys}</kbd>
          </SettingRow>
        ))}
      </SettingGroup>

      <SettingGroup title="Your data">
        <SettingRow label="Download my data" description="Your conversations, memories, preferences and scheduled tasks as one file.">
          <Button variant="secondary" disabled={downloading} onClick={() => void download()}>
            <Download /> {downloading ? "Preparing…" : "Download"}
          </Button>
        </SettingRow>
        <SettingRow label="Archived conversations" description="Conversations you put away. Restore or delete them.">
          <Button variant="secondary" onClick={() => onOpenTab?.("archived")}>
            <Archive /> Open
          </Button>
        </SettingRow>
        <SettingRow label="Files" description="What you uploaded and what the assistant made.">
          <Button variant="secondary" onClick={() => onOpenTab?.("storage")}>
            <HardDrive /> Open
          </Button>
        </SettingRow>
      </SettingGroup>
    </Page>
  );
}
