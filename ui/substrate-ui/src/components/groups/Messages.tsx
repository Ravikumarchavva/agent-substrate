"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, CheckCheck, ChevronDown, Copy, PauseCircle, Reply } from "lucide-react";
import { Button, cn, toast } from "@/design";
import type { GroupEntry } from "@/lib/api/groups";
import { shortTime } from "@/lib/short-time";
import { Attachments } from "./Attachments";
import { Avatar } from "./Avatar";
import { Markdown } from "./Markdown";
import { dayLabel, hueOf, previewOf, sameDay, splitMentions, typingLine } from "./text";

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
};

/** What was said in the group, oldest first, in runs by the same person under day headings, kept at the end while you are at the end. */
export function Messages({ entries, names, avatars, typing, readByAll, onReply }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const [atEnd, setAtEnd] = useState(true);
  const [missed, setMissed] = useState(0);
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
        className="scroll-area h-full"
        // A faint dotted wallpaper, like a messenger's, in the border colour so it follows the theme.
        style={{ backgroundImage: "radial-gradient(var(--border) 1px, transparent 1px)", backgroundSize: "22px 22px" }}
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
              <p key={`d-${entry.seq}`} className="mx-auto my-3 rounded-lg bg-card px-3 py-1 text-xs font-medium uppercase tracking-wide text-muted shadow-sm">
                {dayLabel(entry.at)}
              </p>
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
            return (
              <div key={entry.seq} className="flex flex-col">
                {heading}
                <div className={cn("group flex items-end gap-2", entry.from_user && "flex-row-reverse", startsRun && "mt-2")}>
                  {!entry.from_user && (startsRun ? <Avatar name={entry.sender} src={avatars[entry.sender]} className="size-8 text-xs" /> : <span className="size-8 shrink-0" aria-hidden />)}
                  <div
                    id={`m-${entry.seq}`}
                    className={cn(
                      "min-w-0 max-w-[85%] rounded-2xl px-3 py-2 transition-shadow @3xl:max-w-[70%]",
                      entry.from_user ? "bg-accent/15 text-foreground" : "bg-card text-foreground shadow-sm",
                      startsRun && (entry.from_user ? "rounded-tr-md" : "rounded-tl-md"),
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
                    {entry.text &&
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
                    <p className="mt-0.5 flex items-center justify-end gap-1 text-2xs text-muted">
                      {shortTime(entry.at)}
                      {entry.from_user && (seenByAll ? <CheckCheck className="size-3.5 text-accent" aria-label="Seen by everyone" /> : <Check className="size-3.5" aria-label="Sent" />)}
                    </p>
                  </div>
                  <div className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100">
                    <Button variant="ghost" size="icon-sm" aria-label={`Reply to ${entry.sender}`} onClick={() => onReply(entry)}>
                      <Reply />
                    </Button>
                    {entry.text && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Copy message"
                        onClick={() => void navigator.clipboard.writeText(entry.text).then(() => toast.success("Copied"))}
                      >
                        <Copy />
                      </Button>
                    )}
                  </div>
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
