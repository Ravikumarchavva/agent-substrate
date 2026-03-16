/* ── Field — label + input wrapper ───────────────────────────────────────
 *
 * Wraps any input element with a consistent label style.
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import React from "react";

interface FieldProps {
  label: string;
  required?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function Field({ label, required, children, className = "" }: FieldProps) {
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <label
        className="block text-[11px] font-medium uppercase tracking-wider"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
        {required && <span className="ml-0.5 text-red-400">*</span>}
      </label>
      {children}
    </div>
  );
}
