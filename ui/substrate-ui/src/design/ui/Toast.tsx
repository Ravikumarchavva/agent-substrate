"use client";

import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { useSyncExternalStore } from "react";
import { Button } from "./Button";

/**
 * Feedback for something the user just did (or tried to). Callable from anywhere, components or plain functions:
 *
 *   toast.error("Couldn't delete the thread", "Check your connection and try again.")
 *   toast.success("Renamed")
 *
 * One `<Toaster />` at the app root shows them. Errors stay longer and are announced to screen readers immediately.
 */
type Tone = "success" | "error" | "info";

interface ToastItem {
  id: number;
  tone: Tone;
  title: string;
  description?: string;
}

const MAX_VISIBLE = 4;
const DURATION_MS: Record<Tone, number> = { success: 3500, info: 5000, error: 8000 };

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function dismiss(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

function show(tone: Tone, title: string, description?: string): number {
  const id = nextId++;
  // The same message twice in a row is one toast, not a stack of them.
  if (items.some((t) => t.tone === tone && t.title === title && t.description === description)) return id;
  items = [...items, { id, tone, title, description }].slice(-MAX_VISIBLE);
  emit();
  setTimeout(() => dismiss(id), DURATION_MS[tone]);
  return id;
}

export const toast = {
  success: (title: string, description?: string) => show("success", title, description),
  error: (title: string, description?: string) => show("error", title, description),
  info: (title: string, description?: string) => show("info", title, description),
  dismiss,
};

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => void listeners.delete(l);
};
const snapshot = () => items;
const serverSnapshot = (): ToastItem[] => [];

const ICON = { success: CheckCircle2, error: AlertCircle, info: Info } as const;
const ICON_TONE = { success: "text-success", error: "text-danger", info: "text-muted" } as const;

export function Toaster() {
  const visible = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-[9999] flex flex-col items-center gap-2 px-4 sm:items-end sm:px-6">
      {visible.map((t) => {
        const Icon = ICON[t.tone];
        return (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className="substrate-pop-in pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border border-border bg-card px-3.5 py-3 text-sm shadow-lg"
          >
            <Icon className={`mt-0.5 size-4 shrink-0 ${ICON_TONE[t.tone]}`} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-foreground">{t.title}</p>
              {t.description && <p className="mt-0.5 text-xs text-muted">{t.description}</p>}
            </div>
            <Button variant="ghost" size="icon-sm" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <X />
            </Button>
          </div>
        );
      })}
    </div>
  );
}
