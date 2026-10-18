import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "../cn";

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  /** What ticking it means, in words: always shown, and what a screen reader announces. */
  label: ReactNode;
}

/** A tick box with its label. The whole label is clickable; the box takes the product's selected colour. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(({ className, label, id, ...props }, ref) => {
  const generated = useId();
  const inputId = id ?? generated;
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <input ref={ref} id={inputId} type="checkbox" className="size-4 cursor-pointer rounded border-border accent-accent" {...props} />
      <label htmlFor={inputId} className="cursor-pointer text-xs font-medium text-muted">
        {label}
      </label>
    </div>
  );
});
Checkbox.displayName = "Checkbox";
