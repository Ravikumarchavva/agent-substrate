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
    <div className={cn("grid grid-cols-[90px_minmax(0,1fr)] items-start gap-x-3 gap-y-0.5", className)}>
      <div className="pt-1.5">
        <label className="text-[10px] font-medium leading-none tracking-wide text-(--text-muted) uppercase">
          {label}
          {required && <span className="ml-0.5 text-red-400">*</span>}
        </label>
        {description ? <p className="mt-0.5 text-[9px] leading-snug text-(--text-dim)">{description}</p> : null}
      </div>
      <div className="min-w-0">
        {children}
        {message ? <p className="mt-1 text-[9px] leading-snug text-(--text-dim)">{message}</p> : null}
      </div>
    </div>
  );
}
