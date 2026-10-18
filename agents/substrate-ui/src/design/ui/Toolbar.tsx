import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "../cn";
import { Button, type ButtonProps } from "./Button";

/** A horizontal row of controls. Wraps on narrow screens; every child is a control-scale height, so the row is one line. */
export const Toolbar = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div ref={ref} role="toolbar" className={cn("flex flex-wrap items-center gap-2", className)} {...props} />
));
Toolbar.displayName = "Toolbar";

/** Controls joined into one bordered strip (back/forward, grid/list, S/M/L). */
export const ToolbarGroup = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    role="group"
    className={cn(
      "flex h-control-md shrink-0 items-center overflow-hidden rounded-md border border-border bg-background/50 " +
        "[&>*]:rounded-none [&>*]:border-0 [&>*+*]:border-l [&>*+*]:border-border/60",
      className,
    )}
    {...props}
  />
));
ToolbarGroup.displayName = "ToolbarGroup";

const itemVariants = cva("", { variants: { active: { true: "bg-card-hover text-foreground", false: "" } }, defaultVariants: { active: false } });

/** One segment of a ToolbarGroup; `active` marks the selected one. */
export const ToolbarItem = forwardRef<HTMLButtonElement, Omit<ButtonProps, "variant"> & VariantProps<typeof itemVariants>>(
  ({ className, active, ...props }, ref) => (
    <Button ref={ref} variant="ghost" aria-pressed={active ?? undefined} className={cn(itemVariants({ active }), className)} {...props} />
  ),
);
ToolbarItem.displayName = "ToolbarItem";
