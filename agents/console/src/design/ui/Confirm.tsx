"use client";

import { useSyncExternalStore } from "react";
import { Button } from "./Button";
import { Dialog, DialogContent, DialogFooter } from "./Dialog";

/**
 * Ask before something that can't be undone, in place of `window.confirm`:
 *
 *   if (!(await confirmAction({ title: "Delete this task?", description: "Its run history goes too.", confirmLabel: "Delete", danger: true }))) return;
 *
 * One `<ConfirmHost />` at the app root renders it. Escape, the close button and Cancel all answer `false`.
 */
export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  /** A destructive action: the confirm button is red instead of the call-to-action colour. */
  danger?: boolean;
}

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

let pending: Pending | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function confirmAction(options: ConfirmOptions): Promise<boolean> {
  pending?.resolve(false); // a second question replaces an unanswered first
  return new Promise<boolean>((resolve) => {
    pending = { ...options, resolve };
    emit();
  });
}

function answer(ok: boolean) {
  const current = pending;
  pending = null;
  emit();
  current?.resolve(ok);
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => void listeners.delete(l);
};

export function ConfirmHost() {
  const current = useSyncExternalStore(
    subscribe,
    () => pending,
    () => null,
  );
  return (
    <Dialog open={current !== null} onOpenChange={(open) => !open && answer(false)}>
      {current && (
        <DialogContent title={current.title} description={current.description}>
          <DialogFooter className="mt-0">
            <Button variant="secondary" onClick={() => answer(false)}>
              Cancel
            </Button>
            <Button variant={current.danger ? "danger" : "primary"} className={current.danger ? "border border-danger/40" : undefined} onClick={() => answer(true)}>
              {current.confirmLabel ?? "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  );
}
