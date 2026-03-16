/* ── Badge — status / label pill ─────────────────────────────────────────
 *
 * Variants: draft | saved | success | warning | info
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import React from "react";

interface BadgeProps {
  children: React.ReactNode;
  variant?: "draft" | "saved" | "success" | "warning" | "info";
  className?: string;
}

const VARIANT_STYLES: Record<string, { bg: string; color: string }> = {
  draft:   { bg: "var(--bg-elevated)", color: "var(--text-muted)" },
  saved:   { bg: "rgba(34,197,94,0.15)", color: "#22c55e" },
  success: { bg: "rgba(34,197,94,0.15)", color: "#22c55e" },
  warning: { bg: "rgba(234,179,8,0.15)", color: "#eab308" },
  info:    { bg: "rgba(99,102,241,0.15)", color: "#818cf8" },
};

export function Badge({ children, variant = "info", className = "" }: BadgeProps) {
  const { bg, color } = VARIANT_STYLES[variant] ?? VARIANT_STYLES.info;
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${className}`}
      style={{ background: bg, color }}
    >
      {children}
    </span>
  );
}
