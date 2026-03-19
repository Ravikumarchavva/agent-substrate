/* ── Field — label + input wrapper ───────────────────────────────────────
 *
 * Wraps any input element with a consistent label style.
 * ────────────────────────────────────────────────────────────────────────── */

"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface FieldProps {
  label: string;
  required?: boolean;
  description?: string;
  message?: string;
  children: React.ReactNode;
  className?: string;
}

export function Field({ label, required, description, message, children, className = "" }: FieldProps) {
  return (
    <div className={cn("flex flex-col gap-2.5", className)}>
      <div className="flex flex-col gap-1">
        <label className="text-xs font-semibold leading-none text-(--text)">
          {label}
          {required && <span className="ml-0.5 text-red-400">*</span>}
        </label>
        {description ? <p className="text-[11px] leading-5 text-(--text-dim)">{description}</p> : null}
      </div>
      <div className="min-w-0">
        {children}
        {message ? <p className="text-[11px] leading-5 text-(--text-dim)">{message}</p> : null}
      </div>
    </div>
  );
}
