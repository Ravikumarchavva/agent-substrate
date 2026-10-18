"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef, type HTMLAttributes } from "react";
import { cn } from "../cn";
import { Button } from "./Button";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

/** A centred modal. Focus is trapped and Escape closes it (Radix); `title` is required so the dialog is always labelled. */
export const DialogContent = forwardRef<
  ElementRef<typeof DialogPrimitive.Content>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & { title: string; description?: string }
>(({ className, children, title, description, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 flex-col " +
          "rounded-xl border border-border bg-card p-5 shadow-lg",
        className,
      )}
      {...props}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <DialogPrimitive.Title className="text-base font-semibold text-foreground">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className={cn("mt-1 text-sm text-muted", !description && "sr-only")}>
            {description ?? title}
          </DialogPrimitive.Description>
        </div>
        <DialogPrimitive.Close asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Close">
            <X />
          </Button>
        </DialogPrimitive.Close>
      </div>
      <div className="mt-4 min-h-0 flex-1 overflow-y-auto">{children}</div>
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DialogContent.displayName = "DialogContent";

export function DialogFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mt-5 flex flex-wrap justify-end gap-2", className)} {...props} />;
}
