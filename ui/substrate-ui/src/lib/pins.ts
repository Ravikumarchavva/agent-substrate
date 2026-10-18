import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/_client";

/** A chat you keep at the top of your list and in the sidebar: an agent or a group. Which are pinned is kept on your account (`pinned_at`). */
export type ChatKind = "agent" | "group";
export const chatKey = (kind: ChatKind, id: string) => `${kind}:${id}`;
export const MAX_PINS = 5;

/** Pin or unpin. Returns false when five are pinned already (nothing changed). */
export async function setPinned(kind: ChatKind, id: string, pinned: boolean): Promise<boolean> {
  try {
    if (kind === "agent") await api.setAgentPinned(id, pinned);
    else await api.setGroupPinned(id, pinned);
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) return false;
    throw err;
  }
}

const LEGACY_KEY = "substrate.pinned-chats";

/** Pins used to be kept in this browser only. Move them to the account once, then forget them. Returns whether any were found. */
export async function importLocalPins(): Promise<boolean> {
  let keys: string[] = [];
  try {
    const raw = window.localStorage.getItem(LEGACY_KEY);
    if (!raw) return false;
    const parsed: unknown = JSON.parse(raw);
    keys = Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, MAX_PINS) : [];
    window.localStorage.removeItem(LEGACY_KEY);
  } catch {
    return false;
  }
  for (const key of keys) {
    const [kind, id] = key.split(":");
    if ((kind === "agent" || kind === "group") && id) await setPinned(kind, id, true).catch(() => false);
  }
  return keys.length > 0;
}
