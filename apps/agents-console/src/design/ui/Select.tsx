"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { cva, type VariantProps } from "class-variance-authority";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "../cn";

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectGroup {
  label: string;
  options: SelectOption[];
}

const triggerVariants = cva(
  "flex w-full cursor-pointer items-center justify-between gap-2 rounded-md border border-border bg-background/50 text-left text-foreground " +
    "transition-colors hover:border-border-hover focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent " +
    "data-[placeholder]:text-muted disabled:pointer-events-none disabled:opacity-50",
  {
    variants: { size: { sm: "h-control-sm px-2 text-xs", md: "h-control-md px-2.5 text-xs", lg: "h-control-lg px-3 text-sm" } },
    defaultVariants: { size: "md" },
  },
);

interface SelectProps extends VariantProps<typeof triggerVariants> {
  value: string;
  onValueChange: (value: string) => void;
  /** A flat list, or `groups` for a list under headings. */
  options?: SelectOption[];
  groups?: SelectGroup[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}

/** The one dropdown for choosing from a fixed list. Same trigger as `Input`, same panel as `Menu`. */
export function Select({ value, onValueChange, options, groups, placeholder = "Select…", size, disabled, className, ...rest }: SelectProps) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <SelectPrimitive.Trigger className={cn(triggerVariants({ size }), className)} aria-label={rest["aria-label"]}>
        <span className="min-w-0 truncate">
          <SelectPrimitive.Value placeholder={placeholder} />
        </span>
        <SelectPrimitive.Icon>
          <ChevronDown className="size-3.5 shrink-0 opacity-50" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={6}
          className="z-50 max-h-72 min-w-(--radix-select-trigger-width) overflow-hidden rounded-xl border border-border bg-card shadow-lg"
        >
          <SelectPrimitive.Viewport className="p-1">
            {options?.map((option) => <SelectItem key={option.value} option={option} />)}
            {groups?.map((group) => (
              <SelectPrimitive.Group key={group.label}>
                <SelectPrimitive.Label className="px-2.5 pb-1 pt-2 text-2xs font-semibold uppercase tracking-wider text-muted">
                  {group.label}
                </SelectPrimitive.Label>
                {group.options.map((option) => <SelectItem key={option.value} option={option} />)}
              </SelectPrimitive.Group>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

function SelectItem({ option }: { option: SelectOption }) {
  return (
    <SelectPrimitive.Item
      value={option.value}
      className={cn(
        "flex h-control-md cursor-pointer select-none items-center justify-between gap-2 rounded-md px-2.5 text-sm text-foreground outline-none transition-colors",
        "data-[highlighted]:bg-card-hover data-[state=checked]:font-medium",
      )}
    >
      <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator>
        <Check className="size-3.5 text-accent" />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}
