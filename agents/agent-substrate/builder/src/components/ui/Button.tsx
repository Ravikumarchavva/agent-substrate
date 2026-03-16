/* ── Button — reusable button primitive ───────────────────────────────────
 *
 * Variants: ghost | accent | primary | danger
 * Sizes:    sm | md (default)
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import React from "react";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "ghost" | "accent" | "primary" | "danger";
  size?: "sm" | "md";
  children: React.ReactNode;
}

export function Button({
  variant = "ghost",
  size = "md",
  children,
  className = "",
  style,
  ...props
}: ButtonProps) {
  const palette =
    variant === "primary"
      ? {
          bg: "linear-gradient(135deg, #6366f1 0%, #818cf8 100%)",
          color: "#fff",
          borderColor: "rgba(129,140,248,0.32)",
          boxShadow: "0 14px 26px rgba(99,102,241,0.24)",
        }
      : variant === "danger"
        ? {
            bg: "rgba(239,68,68,0.14)",
            color: "#fca5a5",
            borderColor: "rgba(239,68,68,0.2)",
            boxShadow: "none",
          }
        : variant === "accent"
          ? {
              bg: "var(--bg-elevated)",
              color: "var(--text)",
              borderColor: "var(--border)",
              boxShadow: "none",
            }
          : {
              bg: "var(--bg-surface)",
              color: "var(--text-muted)",
              borderColor: "var(--border)",
              boxShadow: "none",
            };

  const padding = size === "sm" ? "px-4.5" : "px-5";
  const height = size === "sm" ? "h-10" : "h-11";
  const fontSize = size === "sm" ? "text-xs" : "text-sm";
  const minWidth = size === "sm" ? "min-w-[92px]" : "min-w-[108px]";

  return (
    <button
      {...props}
      className={`
        inline-flex items-center justify-center gap-1
        ${padding} ${height} ${fontSize} ${minWidth} rounded-xl font-semibold transition-all
        disabled:opacity-30 disabled:cursor-not-allowed hover:-translate-y-0.5
        ${className}
      `.trim()}
      style={{
        background: palette.bg,
        color: palette.color,
        border: `1px solid ${palette.borderColor}`,
        boxShadow: palette.boxShadow,
        ...style,
      }}
    >
      {children}
    </button>
  );
}
