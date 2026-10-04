import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "../cn";

/** A multi-line `Input`: same border, background and focus ring. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full resize-y rounded-md border border-border bg-background/50 p-3 text-sm leading-relaxed text-foreground placeholder:text-muted " +
        "transition-colors focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
