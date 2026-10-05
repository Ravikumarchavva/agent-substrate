import { useCallback, useSyncExternalStore } from "react";

/** A chat you keep in the sidebar: an agent or a group. Everything else is found under Agents and in Notifications. */
export type ChatKind = "agent" | "group";
export const chatKey = (kind: ChatKind, id: string) => `${kind}:${id}`;

const KEY = "substrate.pinned-chats";
const MAX_PINS = 5;
const NONE: readonly string[] = [];
let cache: { raw: string | null; value: readonly string[] } = { raw: null, value: NONE };
const listeners = new Set<() => void>();

function read(): readonly string[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw === cache.raw) return cache.value;
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    const value = Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, MAX_PINS) : NONE;
    cache = { raw, value };
    return value;
  } catch {
    return NONE;
  }
}

function write(next: string[]) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Pins are a convenience; without storage they just do not stick.
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** The pinned chats (at most five, in the order they were pinned) and a way to pin or unpin one. */
export function usePinnedChats() {
  const pinned = useSyncExternalStore(subscribe, read, () => NONE);
  const toggle = useCallback((key: string) => {
    const current = read();
    if (current.includes(key)) write(current.filter((k) => k !== key));
    else if (current.length < MAX_PINS) write([...current, key]);
    else return false;
    return true;
  }, []);
  return { pinned, toggle, isFull: pinned.length >= MAX_PINS };
}
