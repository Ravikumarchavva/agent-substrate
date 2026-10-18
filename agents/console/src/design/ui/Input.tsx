import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "../cn";

const inputVariants = cva(
  "w-full rounded-md border border-border bg-background/50 text-foreground placeholder:text-muted " +
    "transition-colors focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50",
  {
    variants: { size: { sm: "h-control-sm px-2 text-xs", md: "h-control-md px-2.5 text-xs", lg: "h-control-lg px-3 text-sm" } },
    defaultVariants: { size: "md" },
  },
);

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size">, VariantProps<typeof inputVariants> {}

export const Input = forwardRef<HTMLInputElement, InputProps>(({ className, size, ...props }, ref) => (
  <input ref={ref} className={cn(inputVariants({ size }), className)} {...props} />
));
Input.displayName = "Input";
