"use client";

import { useEffect, useState } from "react";

/**
 * Shown between sending a message and the first word of the reply. Dots straight away; after a second a short label so a slow start
 * never looks like a hang, and after six a plain note that it is still going. It names no internal step, because the app is not told
 * which one is running.
 */
export function WaitingIndicator() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => clearInterval(id);
  }, []);

  const label = seconds >= 6 ? "Still working on it…" : seconds >= 1 ? "Thinking…" : null;
  return (
    <div className="flex items-center gap-2.5 py-2" role="status" aria-live="polite">
      <div className="flex items-center gap-1.5" aria-hidden="true">
        <div className="size-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: "0ms" }} />
        <div className="size-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: "150ms" }} />
        <div className="size-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: "300ms" }} />
      </div>
      {label && <span className="text-xs text-muted">{label}</span>}
    </div>
  );
}
