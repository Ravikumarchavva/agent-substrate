"use client";

import { RotateCw } from "lucide-react";

type Props = {
  onContinue: () => void;
};

export function MaxIterationsCard({ onContinue }: Props) {
  return (
    <div
      className="w-full max-w-sm text-sm overflow-hidden"
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "10px",
      }}
    >
      <div className="px-4 py-3 flex items-start gap-3">
        <span className="text-base mt-0.5">⏸</span>
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-sm" style={{ color: "var(--foreground)" }}>
            Reached step limit
          </p>
          <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
            The agent paused after completing its current iteration. Click Continue to pick up where it left off.
          </p>
        </div>
      </div>
      <div
        className="px-4 pb-3"
        style={{ borderTop: "1px solid var(--border)", paddingTop: "10px" }}
      >
        <button
          onClick={onContinue}
          className="flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-lg cursor-pointer transition-colors"
          style={{ background: "var(--accent)", color: "var(--accent-foreground)" }}
        >
          <RotateCw className="w-3.5 h-3.5" />
          Continue
        </button>
      </div>
    </div>
  );
}
