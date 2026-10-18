"use client";

import * as PopoverPrimitive from "@radix-ui/react-popover";
import { Check, ChevronDown } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { cn } from "../cn";
import { Input } from "./Input";

interface ComboboxProps {
  value: string;
  onValueChange: (value: string) => void;
  options: string[];
  placeholder?: string;
  /** Keep text that is not in the list (a custom timezone). Default true. */
  allowCustom?: boolean;
  className?: string;
}

/** A text field with a filtered list under it, for long lists where typing is faster than scrolling. Same panel as `Select` and `Menu`. */
export function Combobox({ value, onValueChange, options, placeholder, allowCustom = true, className }: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const matches = useMemo(() => {
    const q = value.trim().toLowerCase();
    return q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
  }, [options, value]);

  function choose(option: string) {
    onValueChange(option);
    setOpen(false);
  }

  return (
    <PopoverPrimitive.Root open={open && matches.length > 0} onOpenChange={setOpen}>
      <PopoverPrimitive.Anchor asChild>
        <div className={cn("relative", className)}>
          <Input
            ref={inputRef}
            value={value}
            placeholder={placeholder}
            role="combobox"
            aria-expanded={open}
            aria-autocomplete="list"
            onChange={(e) => {
              onValueChange(e.target.value);
              setActive(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setOpen(true);
                setActive((i) => Math.min(i + 1, matches.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((i) => Math.max(i - 1, 0));
              } else if (e.key === "Enter" && open && matches[active]) {
                e.preventDefault();
                choose(matches[active]);
              } else if (e.key === "Escape") {
                setOpen(false);
              }
            }}
            onBlur={() => {
              if (!allowCustom && !options.includes(value)) onValueChange("");
            }}
            className="pr-8"
          />
          <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 opacity-50" />
        </div>
      </PopoverPrimitive.Anchor>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={6}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={(e) => {
            if (e.target === inputRef.current) e.preventDefault();
          }}
          className="z-50 max-h-72 w-(--radix-popover-trigger-width) overflow-y-auto rounded-xl border border-border bg-card p-1 shadow-lg"
        >
          <ul role="listbox">
            {matches.map((option, i) => (
              <li
                key={option}
                role="option"
                aria-selected={option === value}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(option)}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  "flex h-control-md cursor-pointer items-center justify-between rounded-md px-2.5 text-sm text-foreground",
                  i === active && "bg-card-hover",
                  option === value && "font-medium",
                )}
              >
                {option}
                {option === value && <Check className="size-3.5 text-accent" />}
              </li>
            ))}
          </ul>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
