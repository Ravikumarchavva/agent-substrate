/* ── Textarea — styled resizable textarea ────────────────────────────────
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import React from "react";

interface TextareaProps extends Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, "onChange"> {
  value: string;
  onChange: (value: string) => void;
}

export function Textarea({ value, onChange, className = "", style, ...props }: TextareaProps) {
  return (
    <textarea
      {...props}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`
        w-full min-h-28 rounded-xl border px-3.5 py-3 text-sm outline-none transition-all resize-y
        hover:border-white/10 focus:border-[rgba(99,102,241,0.45)] focus:ring-4 focus:ring-[rgba(99,102,241,0.14)]
        ${className}
      `.trim()}
      style={{
        background: "#242424",
        borderColor: "var(--border)",
        color: "var(--text)",
        ...style,
      }}
    />
  );
}
