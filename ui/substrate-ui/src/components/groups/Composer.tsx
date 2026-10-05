"use client";

import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from "react";
import { Loader2, Plus, Send, X } from "lucide-react";
import { Button, FilePicker, cn, toast } from "@/design";
import { api } from "@/lib/api";
import type { GroupEntry, GroupFile } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";
import { FileTypeIcon } from "@/components/FileTypeIcon";
import { Avatar } from "./Avatar";
import { buildObjectUrl } from "@/lib/api/_client";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { fileSize, mentionChoices, mentionQuery, previewOf } from "./text";

/** Files are uploaded the moment they are chosen, like a chat app, and attached when the message is sent. */
type Pending = { id: string; name: string; size: number; mime: string; status: "uploading" | "ready" | "failed"; file?: GroupFile; /** A local copy of a picture, shown at once and while it uploads. */ preview?: string };

export type ComposerHandle = { addFiles: (files: File[]) => void };

type Props = {
  groupId: string;
  names: string[];
  avatars: Record<string, string | null>;
  replyTo: GroupEntry | null;
  onClearReply: () => void;
  onSend: (text: string, attachments: GroupFile[]) => Promise<void>;
};

const MAX_FILES = 10;

/** Where you write to the group. `@` offers the members; the paperclip, pasting or dropping adds files. Enter sends, Shift+Enter starts a new line. */
export const Composer = forwardRef<ComposerHandle, Props>(function Composer({ groupId, names, avatars, replyTo, onClearReply, onSend }, ref) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending[]>([]);
  const field = useRef<HTMLTextAreaElement>(null);

  const addFiles = useCallback(
    (files: File[]) => {
      const room = MAX_FILES - pending.length;
      if (files.length > room) toast.error(`Up to ${MAX_FILES} files go in one message.`);
      for (const file of files.slice(0, Math.max(room, 0))) {
        const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined;
        setPending((all) => [...all, { id, name: file.name, size: file.size, mime: file.type || "application/octet-stream", status: "uploading", preview }]);
        api
          .uploadGroupFile(groupId, file)
          .then((saved) => setPending((all) => all.map((p) => (p.id === id ? { ...p, status: "ready", file: saved, name: saved.name } : p))))
          .catch((err) => {
            setPending((all) => all.map((p) => (p.id === id ? { ...p, status: "failed" } : p)));
            reportError(`Couldn't upload ${file.name}`, err);
          });
      }
      field.current?.focus();
    },
    [groupId, pending.length],
  );
  useImperativeHandle(ref, () => ({ addFiles }), [addFiles]);

  const removePending = (p: Pending) => {
    if (p.preview) URL.revokeObjectURL(p.preview);
    setPending((all) => all.filter((x) => x.id !== p.id));
  };

  const query = mentionQuery(text.slice(0, caret));
  const choices = query === null ? [] : mentionChoices(query, names);
  const uploading = pending.some((p) => p.status === "uploading");
  const ready = pending.flatMap((p) => (p.file ? [p.file] : []));
  const canSend = (text.trim() !== "" || ready.length > 0) && !uploading && !busy;

  const pick = (name: string) => {
    const before = text.slice(0, caret).replace(/@[\w.-]*$/, `@${name} `);
    setText(before + text.slice(caret));
    requestAnimationFrame(() => {
      field.current?.focus();
      field.current?.setSelectionRange(before.length, before.length);
      setCaret(before.length);
    });
  };

  const send = async () => {
    if (!canSend) return;
    setBusy(true);
    try {
      await onSend(text.trim(), ready);
      setText("");
      pending.forEach((p) => p.preview && URL.revokeObjectURL(p.preview));
      setPending([]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-background px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-2 sm:px-5 sm:pb-4">
      <div className="relative w-full">
        {choices.length > 0 && (
          <ul className="absolute bottom-full left-0 z-10 mb-2 w-56 overflow-hidden rounded-xl border border-border bg-card p-1 shadow-lg">
            {choices.map((name) => (
              <li key={name}>
                <Button variant="ghost" onClick={() => pick(name)} className="h-auto! w-full justify-start gap-2 px-2.5 py-1.5 text-sm font-normal">
                  <Avatar name={name} src={avatars[name]} className="size-6 text-2xs" />@{name}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-col overflow-hidden rounded-3xl bg-card px-3.5 py-2.5 shadow-md">
          {replyTo && (
            <div className="mb-2 flex items-center gap-2 rounded-lg border-l-2 border-accent bg-background/60 px-3 py-1.5 text-xs text-muted">
              <p className="min-w-0 flex-1 truncate">
                Replying to <span className="font-medium text-foreground">{replyTo.sender}</span>: {previewOf(replyTo.text, replyTo.attachments)}
              </p>
              <Button variant="ghost" size="icon-sm" aria-label="Cancel reply" onClick={onClearReply}>
                <X />
              </Button>
            </div>
          )}
          {pending.length > 0 && (
            <ul className="scroll-area flex gap-2 overflow-x-auto pb-3 pt-1">
              {pending.map((p) => (
                <li key={p.id} className={cn("flex w-52 shrink-0 items-center gap-2 rounded-xl border bg-background/60 p-2", p.status === "failed" ? "border-danger/50" : "border-border")}>
                  {p.preview ? (
                    <a
                      href={p.file ? buildObjectUrl(p.file.key) : p.preview}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`Open ${p.name}`}
                      className="block size-10 shrink-0 overflow-hidden rounded-lg bg-badge"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element -- a local blob: preview, nothing to optimise */}
                      <img src={p.preview} alt="" className="size-full object-cover" />
                    </a>
                  ) : (
                    <FileTypeIcon name={p.name} mime={p.mime} size="sm" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-foreground">{p.name}</span>
                    <span className={cn("block text-2xs", p.status === "failed" ? "text-danger" : "text-muted")}>
                      {p.status === "uploading" ? "Uploading…" : p.status === "failed" ? "Failed" : fileSize(p.size)}
                    </span>
                  </span>
                  {p.status === "uploading" ? (
                    <Loader2 className="size-4 shrink-0 animate-spin text-muted" aria-label="Uploading" />
                  ) : (
                    <Button variant="ghost" size="icon-sm" aria-label={`Remove ${p.name}`} onClick={() => removePending(p)}>
                      <X />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <textarea
            ref={field}
            rows={1}
            value={text}
            placeholder="Message the group. @ to address someone."
            onChange={(e) => {
              setText(e.target.value);
              setCaret(e.target.selectionStart);
            }}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
            onPaste={(e) => {
              const files = Array.from(e.clipboardData.files);
              if (files.length > 0) {
                e.preventDefault();
                addFiles(files);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (choices.length > 0 && query) pick(choices[0]);
                else void send();
              }
            }}
            className="max-h-48 w-full resize-none overflow-y-auto bg-transparent px-2 py-2.5 text-base outline-none placeholder:text-muted"
          />
          <div className="flex items-center justify-between pt-1">
            <div className="flex items-center gap-1">
              <FilePicker variant="ghost" size="icon" aria-label="Attach files" onFiles={addFiles}>
                <Plus />
              </FilePicker>
              <VoiceRecorder disabled={busy} onTranscript={(spoken) => setText((current) => (current.trim() ? `${current.trimEnd()} ${spoken}` : spoken))} />
            </div>
            <Button variant="primary" size="icon" aria-label="Send" disabled={!canSend} onClick={() => void send()} className="rounded-full">
              <Send />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
});
