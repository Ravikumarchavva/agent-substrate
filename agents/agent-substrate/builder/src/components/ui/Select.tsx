/* ── Select — styled select dropdown ─────────────────────────────────────
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import React from "react";

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "onChange"> {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
}

export function Select({ value, onChange, options, className = "", style, ...props }: SelectProps) {
  return (
    <select
      {...props}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`
        w-full min-h-11 rounded-xl border px-3.5 py-2.5 pr-10 text-sm outline-none transition-all
        hover:border-white/10 focus:border-[rgba(99,102,241,0.45)] focus:ring-4 focus:ring-[rgba(99,102,241,0.14)]
        ${className}
      `.trim()}
      style={{
        background: "#242424",
        borderColor: "var(--border)",
        color: "var(--text)",
        ...style,
      }}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}
