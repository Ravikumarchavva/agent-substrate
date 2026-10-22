"use client";
import { useCallback, useSyncExternalStore } from "react";

const session = new Map<string, boolean>();
const eventName = "substrate-panel-preference";
const subscribe = (listener: () => void) => {
  window.addEventListener("storage", listener);
  window.addEventListener(eventName, listener);
  return () => {
    window.removeEventListener("storage", listener);
    window.removeEventListener(eventName, listener);
  };
};
/** Persist layout preferences only; query text and credentials never enter this store. */
export function usePanelPreference(name: string, initial: boolean) {
  const key = `substrate-data-layout:${name}`;
  const snapshot = useCallback(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved !== null) return saved === "true";
    } catch {
      /* Browser storage is optional. */
    }
    return session.get(key) ?? initial;
  }, [key, initial]);
  const value = useSyncExternalStore(subscribe, snapshot, () => initial);
  const set = (next: boolean) => {
    session.set(key, next);
    try {
      localStorage.setItem(key, String(next));
    } catch {
      /* Session fallback. */
    }
    window.dispatchEvent(new Event(eventName));
  };
  return [value, set] as const;
}
