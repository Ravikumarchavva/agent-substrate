"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Info, Paperclip } from "lucide-react";
import { Button, cn } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group, GroupEntry, GroupFile } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";
import { Avatar } from "./Avatar";
import { Composer, type ComposerHandle } from "./Composer";
import { GroupInfo } from "./GroupInfo";
import { Messages } from "./Messages";
import { typingLine } from "./text";

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
 * New messages arrive as they are posted: the page keeps one request open to the server and shows what comes back.
 */
export function GroupChat({ group, agents, onBack, onChanged, onDeleted }: Props) {
  const [entries, setEntries] = useState<GroupEntry[]>([]);
  const [typing, setTyping] = useState<string[]>([]);
  const [replyTo, setReplyTo] = useState<GroupEntry | null>(null);
  const [infoOpen, setInfoOpen] = useState(false);
  const [readByAll, setReadByAll] = useState(-1);
  const [dragging, setDragging] = useState(false);
  const composer = useRef<ComposerHandle>(null);
  const dragDepth = useRef(0);
  const names = group.members.map((m) => m.name);
  const avatars = Object.fromEntries(group.members.map((m) => [m.name, m.avatar]));

  useEffect(() => {
    let alive = true;
    let after = -1;
    // Ask, and let the server hold the request until something is said: a message appears the moment it is posted. The first
    // request does not wait, so the page fills at once.
    const loop = async () => {
      let first = true;
      while (alive) {
        // The first look always happens (the history is there when you come back to the tab); after that, nothing is fetched while it is in the background.
        if (document.hidden && !first) {
          await new Promise((r) => setTimeout(r, 1000));
          continue;
        }
        try {
          const next = await api.getGroupMessages(group.id, after, first ? 0 : 2);
          first = false;
          if (!alive) return;
          setTyping(next.working);
        setReadByAll(next.read_by_all);
          if (next.entries.length === 0) continue;
          after = next.latest;
          setEntries((all) => [...all, ...next.entries.filter((e) => !all.some((x) => x.seq === e.seq))]);
          await api.markGroupRead(group.id, next.latest);
          if (alive) onChanged();
        } catch {
          // A dropped request is not worth an alert; try again shortly.
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    };
    void loop();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one loop per group; onChanged is only called, never read
  }, [group.id]);

  const send = useCallback(
    async (text: string, attachments: GroupFile[]) => {
      try {
        const sent = await api.sendGroupMessage(group.id, text, replyTo?.seq ?? null, attachments);
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
      <section
        className={cn("relative flex min-w-0 flex-1 flex-col", infoOpen && "hidden @3xl:flex")}
        onDragEnter={(e) => {
          if (!e.dataTransfer.types.includes("Files")) return;
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragging(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          dragDepth.current = 0;
          setDragging(false);
          composer.current?.addFiles(Array.from(e.dataTransfer.files));
        }}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 border-2 border-dashed border-accent bg-background/90 text-accent">
            <Paperclip className="icon-free size-8" aria-hidden />
            <p className="text-sm font-semibold">Drop to share with {group.name}</p>
          </div>
        )}
        <header className="flex items-center gap-3 border-b border-border px-4 pb-3 pt-16 sm:pt-3">
          <Button variant="ghost" size="icon" aria-label="Back to chats" onClick={onBack} className="xl:hidden">
            <ArrowLeft />
          </Button>
          {group.avatar ? (
            <Avatar name={group.name} src={group.avatar} className="size-10" />
          ) : (
            <div className="flex -space-x-2" aria-hidden>
              {group.members.slice(0, 3).map((m) => (
                <Avatar key={m.agent_id} name={m.name} src={m.avatar} className="size-8 text-xs ring-2 ring-background" />
              ))}
            </div>
          )}
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-semibold text-foreground">{group.name}</h1>
            <p className="truncate text-xs text-muted">{typing.length > 0 ? typingLine(typing) : ["You", ...names].join(", ")}</p>
          </div>
          <Button variant="ghost" size="icon" aria-label="Group info" aria-pressed={infoOpen} onClick={() => setInfoOpen((v) => !v)}>
            <Info />
          </Button>
        </header>
        <Messages entries={entries} names={names} avatars={avatars} typing={typing} readByAll={readByAll} onReply={setReplyTo} />
        <Composer ref={composer} groupId={group.id} names={names} avatars={avatars} replyTo={replyTo} onClearReply={() => setReplyTo(null)} onSend={send} />
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
