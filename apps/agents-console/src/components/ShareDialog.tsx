"use client";

import { useEffect, useState } from "react";
import { Check, Copy, Link2Off } from "lucide-react";
import { Button, Dialog, DialogContent, DialogFooter, Input, toast } from "@/design";
import { api } from "@/lib/api";
import { APP_BASE } from "@/lib/api/_client";
import { reportError } from "@/lib/report-error";
import type { Thread } from "@/types";

/** Share a conversation as a read-only public link. Anyone with the link can read what was said; nothing else (no tool output, no files). */
export function ShareDialog({ thread, onClose }: { thread: Thread | null; onClose: () => void }) {
  const [token, setToken] = useState<string | null | undefined>(undefined); // undefined while loading
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!thread) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset while the new thread's state loads
    setToken(undefined);
    setCopied(false);
    api
      .getShare(thread.id)
      .then((t) => !cancelled && setToken(t))
      .catch((err) => {
        if (!cancelled) setToken(null);
        reportError("Couldn't check the share link", err);
      });
    return () => {
      cancelled = true;
    };
  }, [thread]);

  const url = token ? `${window.location.origin}${APP_BASE}/share/${token}` : "";

  const create = async () => {
    if (!thread) return;
    setBusy(true);
    try {
      setToken(await api.createShare(thread.id));
    } catch (err) {
      reportError("Couldn't create the link", err);
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!thread) return;
    setBusy(true);
    try {
      await api.deleteShare(thread.id);
      setToken(null);
      toast.success("Sharing stopped", "The old link no longer works.");
    } catch (err) {
      reportError("Couldn't stop sharing", err);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Couldn't copy", "Select the link and copy it by hand.");
    }
  };

  return (
    <Dialog open={thread !== null} onOpenChange={(open) => !open && onClose()}>
      {thread && (
        <DialogContent title="Share this conversation" description={`“${thread.name}” as a read-only page.`}>
          {token === undefined ? (
            <p className="text-sm text-muted">Checking…</p>
          ) : token === null ? (
            <>
              <p className="text-sm text-muted">
                Anyone with the link can read the questions and answers, without signing in. Files, tool output and your account details are not
                included. You can stop sharing at any time.
              </p>
              <DialogFooter>
                <Button variant="secondary" onClick={onClose}>
                  Cancel
                </Button>
                <Button variant="primary" disabled={busy} onClick={() => void create()}>
                  Create link
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <div className="flex gap-2">
                <Input readOnly value={url} size="lg" aria-label="Share link" onFocus={(e) => e.currentTarget.select()} />
                <Button variant="primary" size="lg" onClick={() => void copy()}>
                  {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <p className="mt-3 text-xs text-muted">The page shows the whole conversation, including messages sent after you shared it.</p>
              <DialogFooter>
                <Button variant="danger" disabled={busy} onClick={() => void stop()}>
                  <Link2Off /> Stop sharing
                </Button>
                <Button variant="secondary" onClick={onClose}>
                  Done
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      )}
    </Dialog>
  );
}
