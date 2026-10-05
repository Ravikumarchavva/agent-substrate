"use client";

import { useEffect, useRef } from "react";
import { PauseCircle, Reply } from "lucide-react";
import { Button, cn } from "@/design";
import type { GroupEntry } from "@/lib/api/groups";
import { shortTime } from "@/lib/short-time";
import { Avatar } from "./Avatar";
import { hueOf, splitMentions, typingLine } from "./text";

type Props = {
  entries: GroupEntry[];
  /** Names of the members, to highlight when someone is addressed. */
  names: string[];
  typing: string[];
  onReply: (entry: GroupEntry) => void;
};

/** What was said in the group, oldest first, kept scrolled to the end while you are at the end. */
export function Messages({ entries, names, typing, onReply }: Props) {
  const end = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const bySeq = new Map(entries.map((e) => [e.seq, e]));

  useEffect(() => {
    if (pinned.current) end.current?.scrollIntoView({ block: "end" });
  }, [entries, typing.length]);

  return (
    <div
      className="scroll-area min-h-0 flex-1"
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-1 px-4 py-4">
        {entries.map((entry, i) => {
          if (entry.kind === "system") {
            return (
              <p key={entry.seq} className="mx-auto my-2 flex max-w-md items-center gap-2 rounded-full bg-badge px-3 py-1 text-center text-xs text-muted">
                <PauseCircle className="size-3.5 shrink-0" aria-hidden />
                {entry.text}
              </p>
            );
          }
          const previous = entries[i - 1];
          const startsRun = !previous || previous.kind === "system" || previous.sender_id !== entry.sender_id;
          const quoted = entry.reply_to === null ? undefined : bySeq.get(entry.reply_to);
          return (
            <div key={entry.seq} className={cn("group flex items-end gap-2", entry.from_user && "flex-row-reverse", startsRun && "mt-2")}>
              {!entry.from_user && (startsRun ? <Avatar name={entry.sender} className="size-8 text-xs" /> : <span className="size-8 shrink-0" aria-hidden />)}
              <div className={cn("max-w-[85%] min-w-0 rounded-2xl px-3 py-2 text-sm", entry.from_user ? "rounded-br-md bg-bubble text-foreground" : "rounded-bl-md border border-border bg-card text-foreground")}>
                {!entry.from_user && startsRun && (
                  <p className="mb-0.5 text-xs font-semibold" style={{ color: `hsl(${hueOf(entry.sender)} 55% 45%)` }}>
                    {entry.sender}
                  </p>
                )}
                {quoted && (
                  <p className="mb-1 truncate rounded-md border-l-2 border-accent bg-background/60 px-2 py-1 text-xs text-muted">
                    <span className="font-medium text-foreground">{quoted.sender}</span> {quoted.text}
                  </p>
                )}
                <p className="whitespace-pre-wrap wrap-break-word">
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
                <p className="mt-0.5 text-right text-2xs text-muted">{shortTime(entry.at)}</p>
              </div>
              <Button variant="ghost" size="icon-sm" aria-label={`Reply to ${entry.sender}`} onClick={() => onReply(entry)} className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100">
                <Reply />
              </Button>
            </div>
          );
        })}
        {typing.length > 0 && <p className="mt-2 px-10 text-xs italic text-muted">{typingLine(typing)}</p>}
        <div ref={end} />
      </div>
    </div>
  );
}
