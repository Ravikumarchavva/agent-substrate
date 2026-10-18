"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { applyAppearance } from "@/lib/appearance";

type Theme = "light" | "dark";
/** What the user chose: a fixed theme, or "system" to follow the operating system. */
export type ThemePreference = "system" | Theme;

type ThemeContextType = {
  /** The theme in effect now. */
  theme: Theme;
  preference: ThemePreference;
  setPreference: (preference: ThemePreference) => void;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function getSnapshot(): boolean {
  return true;
}

function getServerSnapshot(): boolean {
  return false;
}

const subscribe: (onStoreChange: () => void) => () => void = () => () => {};

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function getInitialPreference(): ThemePreference {
  if (typeof window === "undefined") return "system";
  const saved = localStorage.getItem("substrate-theme");
  return saved === "light" || saved === "dark" ? saved : "system";
}

function paint(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(getInitialPreference);
  const [system, setSystem] = useState<Theme>(() => (typeof window === "undefined" ? "dark" : systemTheme()));
  const mounted = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const isFirst = useRef(true);
  const theme: Theme = preference === "system" ? system : preference;

  useEffect(() => {
    applyAppearance();
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setSystem(query.matches ? "light" : "dark");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (isFirst.current) {
      isFirst.current = false;
      return;
    }
    paint(theme);
    if (preference === "system") localStorage.removeItem("substrate-theme");
    else localStorage.setItem("substrate-theme", preference);
  }, [theme, preference]);

  const setPreference = useCallback((next: ThemePreference) => setPreferenceState(next), []);
  const toggleTheme = useCallback(() => setPreferenceState(theme === "dark" ? "light" : "dark"), [theme]);

  if (!mounted) {
    return <>{children}</>;
  }

  return <ThemeContext.Provider value={{ theme, preference, setPreference, toggleTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}
