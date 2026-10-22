"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import {
  createContext,
  useContext,
  useLayoutEffect,
  useSyncExternalStore,
} from "react";

export type ThemePreference = "light" | "dark" | "system";
type Theme = {
  preference: ThemePreference;
  resolved: "light" | "dark";
  setPreference: (value: ThemePreference) => void;
};
const ThemeContext = createContext<Theme | null>(null);
const key = "substrate-data-theme";
let volatilePreference: ThemePreference = "system";
function snapshot() {
  let preference = volatilePreference;
  try {
    const saved = localStorage.getItem(key);
    if (saved === "light" || saved === "dark" || saved === "system")
      preference = saved;
  } catch {
    /* Browser storage is optional. */
  }
  const resolved =
    preference === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;
  return `${preference}:${resolved}`;
}
function subscribe(listener: () => void) {
  const media = matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", listener);
  window.addEventListener("storage", listener);
  window.addEventListener("substrate-theme", listener);
  return () => {
    media.removeEventListener("change", listener);
    window.removeEventListener("storage", listener);
    window.removeEventListener("substrate-theme", listener);
  };
}
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const value = useSyncExternalStore(subscribe, snapshot, () => "system:light");
  const [preference, resolved] = value.split(":") as [
    ThemePreference,
    "light" | "dark",
  ];
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }, [resolved]);
  function setPreference(next: ThemePreference) {
    volatilePreference = next;
    try {
      localStorage.setItem(key, next);
    } catch {
      /* Still support this session. */
    }
    window.dispatchEvent(new Event("substrate-theme"));
  }
  return (
    <ThemeContext.Provider value={{ preference, resolved, setPreference }}>
      {children}
    </ThemeContext.Provider>
  );
}
export function useTheme(): Theme {
  const theme = useContext(ThemeContext);
  if (!theme) throw new Error("ThemeProvider is required");
  return theme;
}
export function ThemeSwitch() {
  const { preference, setPreference } = useTheme();
  return (
    <div className="theme-switch" role="group" aria-label="Appearance">
      {(
        [
          ["light", Sun],
          ["dark", Moon],
          ["system", Monitor],
        ] as const
      ).map(([value, Icon]) => (
        <button
          key={value}
          title={`${value[0].toUpperCase()}${value.slice(1)} theme`}
          aria-label={`Use ${value} theme`}
          aria-pressed={preference === value}
          onClick={() => setPreference(value)}
        >
          <Icon size={14} />
          <span>
            {value[0].toUpperCase()}
            {value.slice(1)}
          </span>
        </button>
      ))}
    </div>
  );
}
