"use client";

import { cn } from "../cn";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

/**
 * Two to four mutually exclusive choices shown as one pill group (Theme: System / Light / Dark). The selected one is violet, as everywhere
 * else in the product. Arrow keys move between them; it is a radio group to a screen reader.
 */
export function Segmented<T extends string>({ value, onChange, options, label, className }: { value: T; onChange: (value: T) => void; options: SegmentedOption<T>[]; label: string; className?: string }) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex h-control-md shrink-0 items-center gap-0.5 rounded-md border border-border bg-background/50 p-0.5", className)}
      onKeyDown={(e) => {
        const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        const at = options.findIndex((o) => o.value === value);
        onChange(options[(at + step + options.length) % options.length].value);
      }}
    >
      {options.map((o) => {
        const selected = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(o.value)}
            className={cn(
              "h-full cursor-pointer rounded px-3 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              selected ? "bg-accent/15 text-accent" : "text-muted hover:text-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
