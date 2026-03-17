/* ── ThemeToggle — sun/moon button, persisted to localStorage ────────────
 *
 * Writes `data-theme="light" | "dark"` to `<html>`.
 * SSR-safe: defaults to "dark" on first load.
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "./ui/Button";

export function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    const saved = (localStorage.getItem("builder-theme") ?? "dark") as "dark" | "light";
    setTheme(saved);
  }, []);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("builder-theme", next);
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      className="h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-muted"
    >
      {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}
