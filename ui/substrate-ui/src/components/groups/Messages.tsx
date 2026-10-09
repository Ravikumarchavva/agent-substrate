"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, Check, CheckCheck, ChevronDown, PauseCircle } from "lucide-react";
import { Button, Textarea, cn, confirmAction } from "@/design";
import type { GroupEntry } from "@/lib/api/groups";
import { shortTime } from "@/lib/short-time";
import { DayDivider } from "@/components/chats/DayDivider";
import { Attachments } from "./Attachments";
import { Avatar } from "./Avatar";
import { Markdown } from "./Markdown";
import { MessageActions } from "./MessageActions";
import { Reactions } from "./Reactions";
import { hueOf, previewOf, sameDay, splitMentions, typingLine } from "./text";

type Props = {
  entries: GroupEntry[];
  /** Names of the members, to highlight when someone is addressed. */
  names: string[];
  /** Each member's picture by name. */
  avatars: Record<string, string | null>;
  typing: string[];
  /** The latest entry every agent has read; your messages up to it show as seen. */
  readByAll: number;
  onReply: (entry: GroupEntry) => void;
  onReact: (entry: GroupEntry, emoji: string) => void;
  /** Change the text of your own message. */
  onEdit: (entry: GroupEntry, text: string) => Promise<void>;
  /** Take back your own message. */
  onDelete: (entry: GroupEntry) => Promise<void>;
  /** A conversation you may read but not take part in: no actions, no ticks. */
  readOnly?: boolean;
};

/** What was said in the group, oldest first, in runs by the same person under day headings, kept at the end while you are at the end. */
export function Messages({ entries, names, avatars, typing, readByAll, onReply, onReact, onEdit, onDelete, readOnly = false }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const [atEnd, setAtEnd] = useState(true);
  const [missed, setMissed] = useState(0);
  const [editing, setEditing] = useState<{ seq: number; text: string } | null>(null);
  const seen = useRef(entries.length);
  const bySeq = new Map(entries.map((e) => [e.seq, e]));

  useEffect(() => {
    const added = entries.length - seen.current;
    seen.current = entries.length;
    if (atEnd) end.current?.scrollIntoView({ block: "end" });
    else if (added > 0 && !entries[entries.length - 1]?.from_user) setMissed((n) => n + added);
  }, [entries, atEnd, typing.length]);

  const toEnd = useCallback(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
    setMissed(0);
  }, []);

  const remove = async (entry: GroupEntry) => {
    if (!(await confirmAction({ title: "Delete this message?", description: "It is removed for everyone in the group, and cannot be brought back.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await onDelete(entry);
    } catch {
      // already reported where it failed
    }
  };

  const jumpTo = (seq: number) => {
    const el = document.getElementById(`m-${seq}`);
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("ring-2", "ring-accent");
    setTimeout(() => el.classList.remove("ring-2", "ring-accent"), 1200);
  };

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scroller}
        className="scroll-area chat-wallpaper h-full"
        onScroll={(e) => {
          const el = e.currentTarget;
          const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          setAtEnd(near);
          if (near) setMissed(0);
        }}
      >
        <div className="flex w-full flex-col gap-1 px-3 py-4 sm:px-5">
          {entries.map((entry, i) => {
            const previous = entries[i - 1];
            const newDay = !previous || !sameDay(previous.at, entry.at);
            const heading = newDay && (
              <DayDivider key={`d-${entry.seq}`} at={entry.at} />
            );
            if (entry.kind === "system") {
              return (
                <div key={entry.seq} className="flex flex-col">
                  {heading}
                  <p className="mx-auto my-2 flex max-w-md items-center gap-2 rounded-full bg-badge px-3 py-1 text-center text-xs text-muted">
                    <PauseCircle className="size-3.5 shrink-0" aria-hidden />
                    {entry.text}
                  </p>
                </div>
              );
            }
            const startsRun = newDay || previous.kind === "system" || previous.sender_id !== entry.sender_id;
            const quoted = entry.reply_to === null ? undefined : bySeq.get(entry.reply_to);
            const seenByAll = entry.seq <= readByAll;
            const deleted = entry.deleted_at != null;
            const own = entry.from_user && !deleted && !readOnly;
            const isEditing = editing?.seq === entry.seq;
            return (
              <div key={entry.seq} className="flex flex-col">
                {heading}
                <div className={cn("group flex items-end gap-2", entry.from_user && "flex-row-reverse", startsRun && "mt-2")}>
                  {!entry.from_user && (startsRun ? <Avatar name={entry.sender} src={avatars[entry.sender]} className="size-8 text-xs" /> : <span className="size-8 shrink-0" aria-hidden />)}
                  <div
                    id={`m-${entry.seq}`}
                    className={cn(
                      "min-w-0 max-w-[85%] rounded-xl px-3 py-2 transition-shadow @3xl:max-w-[70%]",
                      entry.from_user ? "bg-accent/15 text-foreground" : "bg-card text-foreground shadow-sm",
                      startsRun && (entry.from_user ? "rounded-tr-sm" : "rounded-tl-sm"),
                    )}
                  >
                    {!entry.from_user && startsRun && (
                      <p className="mb-0.5 text-xs font-semibold" style={{ color: `hsl(${hueOf(entry.sender)} 55% 45%)` }}>
                        {entry.sender}
                      </p>
                    )}
                    {quoted && (
                      <Button
                        variant="ghost"
                        onClick={() => jumpTo(quoted.seq)}
                        className="mb-1 block h-auto! w-full justify-start truncate rounded-md border-l-2 border-accent bg-background/60 px-2 py-1 text-left text-xs font-normal text-muted hover:bg-background"
                      >
                        <span className="font-medium text-foreground">{quoted.sender}</span> {previewOf(quoted.text, quoted.attachments)}
                      </Button>
                    )}
                    <Attachments files={entry.attachments} />
                    {deleted && (
                      <p className="flex items-center gap-1.5 text-sm italic text-muted">
                        <Ban className="size-3.5" aria-hidden /> This message was deleted
                      </p>
                    )}
                    {isEditing && (
                      <div className="min-w-64 space-y-2">
                        <Textarea
                          autoFocus
                          rows={2}
                          value={editing.text}
                          aria-label="Edit message"
                          onChange={(e) => setEditing({ seq: entry.seq, text: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") setEditing(null);
                            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && editing.text.trim()) {
                              e.preventDefault();
                              void onEdit(entry, editing.text.trim()).then(() => setEditing(null), () => undefined);
                            }
                          }}
                        />
                        <div className="flex justify-end gap-2">
                          <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                            Cancel
                          </Button>
                          <Button size="sm" variant="primary" disabled={!editing.text.trim() || editing.text.trim() === entry.text} onClick={() => void onEdit(entry, editing.text.trim()).then(() => setEditing(null), () => undefined)}>
                            Save
                          </Button>
                        </div>
                      </div>
                    )}
                    {entry.text && !isEditing &&
                      (entry.from_user ? (
                        <p className="whitespace-pre-wrap text-sm wrap-break-word">
                          {splitMentions(entry.text, names).map((piece, k) =>
                            piece.mention ? (
                              <span key={k} className="font-medium text-accent">
                                {piece.text}
                              </span>
                            ) : (
                              <span key={k}>{piece.text}</span>
                            ),
                          )}
                        </p>
                      ) : (
                        <Markdown>{entry.text}</Markdown>
                      ))}
                    <Reactions reactions={entry.reactions ?? []} onReact={(emoji) => onReact(entry, emoji)} />
                    <p className="mt-0.5 flex items-center justify-end gap-1 text-2xs text-muted">
                      {entry.edited_at && !deleted && <span>edited</span>}
                      {shortTime(entry.at)}
                      {entry.from_user && !readOnly && (seenByAll ? <CheckCheck className="size-3.5 text-accent" aria-label="Seen by everyone" /> : <Check className="size-3.5" aria-label="Sent" />)}
                    </p>
                  </div>
                  {!deleted && !isEditing && !readOnly && (
                    <MessageActions
                      entry={entry}
                      onReply={() => onReply(entry)}
                      onReact={(emoji) => onReact(entry, emoji)}
                      onEdit={own ? () => setEditing({ seq: entry.seq, text: entry.text }) : undefined}
                      onDelete={own ? () => void remove(entry) : undefined}
                    />
                  )}
                </div>
              </div>
            );
          })}
          {typing.length > 0 && <p className="mt-2 px-10 text-xs italic text-muted">{typingLine(typing)}</p>}
          <div ref={end} />
        </div>
      </div>
      {!atEnd && (
        <Button variant="secondary" size="icon" aria-label={missed > 0 ? `${missed} new messages, go to the latest` : "Go to the latest"} onClick={toEnd} className="absolute bottom-4 right-6 rounded-full shadow-lg">
          <ChevronDown />
          {missed > 0 && <span className="absolute -right-1 -top-2 min-w-5 rounded-full bg-accent px-1 text-2xs font-semibold leading-5 text-accent-foreground">{missed}</span>}
        </Button>
      )}
    </div>
  );
}
