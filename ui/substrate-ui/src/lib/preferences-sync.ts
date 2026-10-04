import { api } from "@/lib/api";
import {
  CHAT_MODEL_STORAGE_KEY,
  MODEL_PREFERENCES_UPDATED_EVENT,
  REALTIME_MODEL_STORAGE_KEY,
  REALTIME_VOICE_STORAGE_KEY,
  REASONING_STORAGE_KEY,
  STT_MODEL_STORAGE_KEY,
  TTS_MODEL_STORAGE_KEY,
  TTS_PLAYBACK_RATE_STORAGE_KEY,
  TTS_VOICE_STORAGE_KEY,
} from "@/lib/model-preferences";

/**
 * Preferences live on the account (so they follow the user to any browser) and are mirrored in localStorage (so the composer can read
 * them synchronously when sending). `pullPreferences` copies the account's into the browser when someone signs in;
 * `schedulePreferencesPush` copies the browser's back after a change. An account with nothing saved yet adopts what this browser already
 * has, so nobody loses the choices they made before this existed.
 */
export const CUSTOM_INSTRUCTIONS_KEY = "system_instructions_override";
export const TIMEZONE_KEY = "user_timezone";

const MODEL_KEYS = [
  CHAT_MODEL_STORAGE_KEY,
  STT_MODEL_STORAGE_KEY,
  TTS_MODEL_STORAGE_KEY,
  TTS_VOICE_STORAGE_KEY,
  TTS_PLAYBACK_RATE_STORAGE_KEY,
  REALTIME_MODEL_STORAGE_KEY,
  REALTIME_VOICE_STORAGE_KEY,
  REASONING_STORAGE_KEY,
] as const;

const PUSH_DELAY_MS = 800;
let timer: ReturnType<typeof setTimeout> | undefined;
let applying = false;

function readLocal() {
  const models: Record<string, string> = {};
  for (const key of MODEL_KEYS) {
    const value = localStorage.getItem(key);
    if (value) models[key] = value;
  }
  return {
    custom_instructions: localStorage.getItem(CUSTOM_INSTRUCTIONS_KEY)?.trim() ?? "",
    timezone: localStorage.getItem(TIMEZONE_KEY)?.trim() ?? "",
    models,
  };
}

async function push(): Promise<void> {
  try {
    await api.putPreferences(readLocal());
  } catch {
    // The choice is already in this browser; it is sent again with the next change.
  }
}

/** Send this browser's preferences to the account, shortly after the last change. */
export function schedulePreferencesPush(): void {
  if (typeof window === "undefined" || applying) return;
  clearTimeout(timer);
  timer = setTimeout(() => void push(), PUSH_DELAY_MS);
}

function set(key: string, value: string) {
  if (value) localStorage.setItem(key, value);
  else localStorage.removeItem(key);
}

/** Bring the account's preferences into this browser (or, for an account with none, the other way round). */
export async function pullPreferences(): Promise<void> {
  let remote;
  try {
    remote = await api.getPreferences();
  } catch {
    return; // offline or signed out: keep what this browser has
  }
  const empty = !remote.custom_instructions && !remote.timezone && Object.keys(remote.models).length === 0;
  if (empty) {
    const local = readLocal();
    if (local.custom_instructions || local.timezone || Object.keys(local.models).length) await push();
    return;
  }
  applying = true;
  try {
    set(CUSTOM_INSTRUCTIONS_KEY, remote.custom_instructions);
    set(TIMEZONE_KEY, remote.timezone);
    for (const key of MODEL_KEYS) set(key, remote.models[key] ?? "");
    window.dispatchEvent(new CustomEvent(MODEL_PREFERENCES_UPDATED_EVENT, { detail: { key: null, value: null } }));
  } finally {
    applying = false;
  }
}

/** Push after any model/voice choice made in the app (the picker, the settings page). Returns the cleanup. */
export function watchPreferenceChanges(): () => void {
  const onChange = () => schedulePreferencesPush();
  window.addEventListener(MODEL_PREFERENCES_UPDATED_EVENT, onChange);
  return () => window.removeEventListener(MODEL_PREFERENCES_UPDATED_EVENT, onChange);
}
