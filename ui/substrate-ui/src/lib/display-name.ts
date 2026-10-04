import { useEffect, useState } from "react";

/** What the user asked to be called, kept in this browser (and synced to the account by `preferences-sync.ts`). Empty means the name on their account. */
export const DISPLAY_NAME_KEY = "display_name";
export const DISPLAY_NAME_EVENT = "substrate:display-name";

export function getDisplayName(): string {
  try {
    return localStorage.getItem(DISPLAY_NAME_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

/** The name to show: the one chosen, else the account's, else `fallback`. */
export function nameToShow(accountName: string | null | undefined, fallback = ""): string {
  return getDisplayName() || accountName?.trim() || fallback;
}

export function setDisplayName(name: string): void {
  const value = name.split(/\s+/).filter(Boolean).join(" ").slice(0, 60);
  try {
    if (value) localStorage.setItem(DISPLAY_NAME_KEY, value);
    else localStorage.removeItem(DISPLAY_NAME_KEY);
  } catch {
    // Storage blocked: the name is not kept.
  }
  window.dispatchEvent(new Event(DISPLAY_NAME_EVENT));
}

/** The name to show, kept current when it is changed in Settings or arrives from the account. Starts on the account name so server and browser render alike. */
export function useDisplayName(accountName: string | null | undefined, fallback = ""): string {
  const [name, setName] = useState(accountName?.trim() || fallback);
  useEffect(() => {
    const update = () => setName(nameToShow(accountName, fallback));
    update();
    window.addEventListener(DISPLAY_NAME_EVENT, update);
    return () => window.removeEventListener(DISPLAY_NAME_EVENT, update);
  }, [accountName, fallback]);
  return name;
}
