import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "../cn";

const badgeVariants = cva("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-semibold leading-none tabular-nums", {
  variants: {
    tone: {
      neutral: "bg-badge text-badge-foreground",
      accent: "bg-accent/15 text-accent",
      success: "bg-success/15 text-success",
      warning: "bg-warning/15 text-warning",
      danger: "bg-danger/15 text-danger",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
