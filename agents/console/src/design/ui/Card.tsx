import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "../cn";

/** A bordered panel. `interactive` adds the hover state for cards that are clicked. */
export const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement> & { interactive?: boolean }>(
  ({ className, interactive = false, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border border-border bg-card",
        interactive && "cursor-pointer transition-colors hover:border-border-hover hover:bg-card-hover",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";
