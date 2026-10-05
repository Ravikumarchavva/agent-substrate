"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Info } from "lucide-react";
import { Button, cn } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group, GroupEntry } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";
import { Avatar } from "./Avatar";
import { Composer } from "./Composer";
import { GroupInfo } from "./GroupInfo";
import { Messages } from "./Messages";
import { typingLine } from "./text";

const POLL_MS = 1500;

type Props = {
  group: Group;
  agents: Agent[];
  onBack: () => void;
  /** The group changed (members, name) or was read: the lists that show it should refresh. */
  onChanged: () => void;
  onDeleted: () => void;
};

/**
 * One group: the conversation in the middle and, beside it (on a wide screen) or in its place (on a narrow one), who is in it.
 * New messages are fetched every moment the page is visible, only those after the last one seen.
 */
export function GroupChat({ group, agents, onBack, onChanged, onDeleted }: Props) {
  const [entries, setEntries] = useState<GroupEntry[]>([]);
  const [typing, setTyping] = useState<string[]>([]);
  const [replyTo, setReplyTo] = useState<GroupEntry | null>(null);
  const [infoOpen, setInfoOpen] = useState(false);
  const names = group.members.map((m) => m.name);

  useEffect(() => {
    let alive = true;
    let after = -1;
    const tick = async () => {
      if (document.hidden) return;
      try {
        const next = await api.getGroupMessages(group.id, after);
        if (!alive) return;
        setTyping(next.working);
        if (next.entries.length === 0) return;
        after = next.latest;
        setEntries((all) => [...all, ...next.entries.filter((e) => !all.some((x) => x.seq === e.seq))]);
        await api.markGroupRead(group.id, next.latest);
        if (alive) onChanged();
      } catch {
        // The next tick tries again; a dropped poll is not worth an alert.
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one poll loop per group; onChanged is only called, never read
  }, [group.id]);

  const send = useCallback(
    async (text: string) => {
      try {
        const sent = await api.sendGroupMessage(group.id, text, replyTo?.seq ?? null);
        setEntries((all) => (all.some((x) => x.seq === sent.seq) ? all : [...all, sent]));
        setReplyTo(null);
      } catch (err) {
        reportError("Couldn't send that", err);
        throw err;
      }
    },
    [group.id, replyTo],
  );

  return (
    <div className="@container flex h-full min-h-0 w-full bg-background text-foreground">
      <section className={cn("flex min-w-0 flex-1 flex-col", infoOpen && "hidden @3xl:flex")}>
        <header className="flex items-center gap-3 border-b border-border px-4 pb-3 pt-16 sm:pt-3">
          <Button variant="ghost" size="icon" aria-label="Back to groups" onClick={onBack} className="@3xl:hidden">
            <ArrowLeft />
          </Button>
          <div className="flex -space-x-2" aria-hidden>
            {group.members.slice(0, 3).map((m) => (
              <Avatar key={m.agent_id} name={m.name} className="size-8 text-xs ring-2 ring-background" />
            ))}
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-semibold text-foreground">{group.name}</h1>
            <p className="truncate text-xs text-muted">{typing.length > 0 ? typingLine(typing) : ["You", ...names].join(", ")}</p>
          </div>
          <Button variant="ghost" size="icon" aria-label="Group info" aria-pressed={infoOpen} onClick={() => setInfoOpen((v) => !v)}>
            <Info />
          </Button>
        </header>
        <Messages entries={entries} names={names} typing={typing} onReply={setReplyTo} />
        <Composer names={names} replyTo={replyTo} onClearReply={() => setReplyTo(null)} onSend={send} />
      </section>
      {infoOpen && (
        <aside className="scroll-area h-full min-h-0 w-full border-l border-border @3xl:w-96 @3xl:shrink-0">
          <div className="px-4 pb-8 pt-16 sm:px-6 sm:pt-6">
            <div className="mb-4 flex items-center gap-2 @3xl:hidden">
              <Button variant="ghost" size="icon" aria-label="Back to the conversation" onClick={() => setInfoOpen(false)}>
                <ArrowLeft />
              </Button>
              <h2 className="text-sm font-semibold">Group info</h2>
            </div>
            <GroupInfo group={group} agents={agents} onChanged={onChanged} onDeleted={onDeleted} />
          </div>
        </aside>
      )}
    </div>
  );
}
