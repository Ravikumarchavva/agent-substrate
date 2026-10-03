import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../cn";

/** Every button in the product. Height comes from the control scale, so buttons in one row always align. */
export const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer select-none items-center justify-center gap-1.5 whitespace-nowrap font-medium " +
    "transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
    "disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent-2 text-accent-2-foreground hover:bg-accent-2-hover",
        accent: "bg-accent text-accent-foreground hover:bg-accent-hover",
        secondary: "border border-border bg-background/50 text-foreground/80 hover:bg-card-hover hover:text-foreground",
        ghost: "text-muted hover:bg-card-hover hover:text-foreground",
        danger: "text-danger hover:bg-danger/10",
      },
      size: {
        sm: "h-control-sm rounded-md px-2 text-xs",
        md: "h-control-md rounded-md px-2.5 text-xs",
        lg: "h-control-lg rounded-lg px-4 text-sm [&_svg]:size-4",
        icon: "size-control-md rounded-md text-xs [&_svg]:size-4",
        "icon-sm": "size-control-sm rounded-md text-xs",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Render the child element (a link, say) with the button's styles instead of a `<button>`. */
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type = "button", ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} type={asChild ? undefined : type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = "Button";
