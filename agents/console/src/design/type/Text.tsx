import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "../cn";

const textVariants = cva("", {
  variants: {
    size: { body: "text-sm", small: "text-xs", meta: "text-2xs" },
    tone: { default: "text-foreground", muted: "text-muted" },
  },
  defaultVariants: { size: "body", tone: "default" },
});

export function Text({ size, tone, className, ...props }: HTMLAttributes<HTMLParagraphElement> & VariantProps<typeof textVariants>) {
  return <p className={cn(textVariants({ size, tone }), className)} {...props} />;
}
