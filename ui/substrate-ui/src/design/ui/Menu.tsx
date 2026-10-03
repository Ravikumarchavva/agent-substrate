"use client";

import * as MenuPrimitive from "@radix-ui/react-dropdown-menu";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "../cn";

export const Menu = MenuPrimitive.Root;
export const MenuTrigger = MenuPrimitive.Trigger;

export const MenuContent = forwardRef<ElementRef<typeof MenuPrimitive.Content>, ComponentPropsWithoutRef<typeof MenuPrimitive.Content>>(
  ({ className, sideOffset = 6, align = "start", ...props }, ref) => (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        align={align}
        className={cn("z-50 min-w-44 overflow-hidden rounded-xl border border-border bg-card p-1 shadow-lg", className)}
        {...props}
      />
    </MenuPrimitive.Portal>
  ),
);
MenuContent.displayName = "MenuContent";

export const MenuItem = forwardRef<
  ElementRef<typeof MenuPrimitive.Item>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.Item> & { tone?: "default" | "danger" }
>(({ className, tone = "default", ...props }, ref) => (
  <MenuPrimitive.Item
    ref={ref}
    className={cn(
      "flex min-h-control-md cursor-pointer select-none items-center gap-2 rounded-md px-2.5 py-1 text-sm outline-none transition-colors " +
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
      tone === "danger" ? "text-danger data-[highlighted]:bg-danger/10" : "text-foreground data-[highlighted]:bg-card-hover [&_svg]:text-muted",
      className,
    )}
    {...props}
  />
));
MenuItem.displayName = "MenuItem";

export const MenuLabel = forwardRef<ElementRef<typeof MenuPrimitive.Label>, ComponentPropsWithoutRef<typeof MenuPrimitive.Label>>(
  ({ className, ...props }, ref) => <MenuPrimitive.Label ref={ref} className={cn("px-2.5 py-2", className)} {...props} />,
);
MenuLabel.displayName = "MenuLabel";

export const MenuSeparator = forwardRef<ElementRef<typeof MenuPrimitive.Separator>, ComponentPropsWithoutRef<typeof MenuPrimitive.Separator>>(
  ({ className, ...props }, ref) => <MenuPrimitive.Separator ref={ref} className={cn("-mx-1 my-1 h-px bg-border", className)} {...props} />,
);
MenuSeparator.displayName = "MenuSeparator";
