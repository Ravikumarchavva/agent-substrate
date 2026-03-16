/* ── Input — styled text / number input ──────────────────────────────────
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import React from "react";

interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange"> {
  value: string | number;
  onChange: (value: string) => void;
}

export function Input({ value, onChange, className = "", style, ...props }: InputProps) {
  return (
    <input
      {...props}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`
        w-full min-h-11 rounded-xl border px-3.5 py-2.5 text-sm outline-none transition-all
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
