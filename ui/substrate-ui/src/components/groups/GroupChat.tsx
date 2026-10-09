"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Info, Paperclip } from "lucide-react";
import { Button, cn } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group, GroupEntry, GroupFile } from "@/lib/api/groups";
import { groupFeed } from "@/lib/group-feed";
import { reportError } from "@/lib/report-error";
import { Avatar } from "./Avatar";
import { Composer, type ComposerHandle } from "./Composer";
import { ChatHeader } from "@/components/chats/ChatHeader";
import { GroupInfo } from "./GroupInfo";
import { foldEntries } from "./entries";
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
 * New messages arrive as they are posted: the page keeps one stream open with the server (the feed) and shows what comes down it.
 */
const HISTORY_PAGE = 200;

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

  // What has been said, from the start, and then whatever the feed brings: it is open for the whole visit, so the page has nothing to ask for.
  const latest = useRef(-1);
  const markRead = useCallback(() => {
    if (document.hidden || latest.current < 0) return;
    api.markGroupRead(group.id, latest.current).catch(() => undefined);
  }, [group.id]);
  useEffect(() => {
    let alive = true;
    const off = groupFeed.subscribe((event) => {
      if (!alive || !("chat" in event) || event.chat !== group.id) return;
      if (event.type === "entry") {
        latest.current = Math.max(latest.current, event.entry.seq);
        setEntries((all) => foldEntries(all, [event.entry]));
        if (event.entry.kind === "message" && !event.entry.from_user) markRead();
      } else if (event.type === "working") setTyping(event.names);
      else if (event.type === "read") setReadByAll(event.by_all);
    });
    // Subscribed first, so nothing said while the history loads is missed; entries that turn up twice are folded into one.
    void (async () => {
      let after = -1;
      for (;;) {
        const page = await api.getGroupMessages(group.id, after, 0).catch(() => null);
        if (!alive || page === null) return;
        setTyping(page.working);
        setReadByAll(page.read_by_all);
        if (page.entries.length === 0) break;
        setEntries((all) => foldEntries(all, page.entries));
        groupFeed.note(group.id, page.latest);
        latest.current = Math.max(latest.current, page.latest);
        after = page.latest;
        if (page.entries.length < HISTORY_PAGE) break;
      }
      markRead();
      if (alive) onChanged();
    })();
    // Coming back to the tab is reading what arrived while it was away.
    const visible = () => !document.hidden && markRead();
    document.addEventListener("visibilitychange", visible);
    return () => {
      alive = false;
      off();
      document.removeEventListener("visibilitychange", visible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one subscription per group; onChanged and markRead are only called, never read
  }, [group.id]);

  const send = useCallback(
    async (text: string, attachments: GroupFile[]) => {
      try {
        const sent = await api.sendGroupMessage(group.id, text, replyTo?.seq ?? null, attachments);
        setEntries((all) => foldEntries(all, [sent]));
        setReplyTo(null);
      } catch (err) {
        reportError("Couldn't send that", err);
        throw err;
      }
    },
    [group.id, replyTo],
  );

  // The entry that records a change comes back from the server; folding it in shows the change at once, and the poll that carries it later changes nothing.
  const change = useCallback(
    async (what: string, run: () => Promise<GroupEntry>) => {
      try {
        const marker = await run();
        setEntries((all) => foldEntries(all, [marker]));
      } catch (err) {
        reportError(what, err);
        throw err;
      }
    },
    [],
  );
  const edit = useCallback((entry: GroupEntry, text: string) => change("Couldn't edit that", () => api.editGroupMessage(group.id, entry.seq, text)), [change, group.id]);
  const remove = useCallback((entry: GroupEntry) => change("Couldn't delete that", () => api.deleteGroupMessage(group.id, entry.seq)), [change, group.id]);
  const react = useCallback((entry: GroupEntry, emoji: string) => void change("Couldn't react to that", () => api.reactToGroupMessage(group.id, entry.seq, emoji)).catch(() => undefined), [change, group.id]);

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
        <ChatHeader
          onBack={onBack}
          title={group.name}
          subtitle={typing.length > 0 ? typingLine(typing) : ["You", ...names].join(", ")}
          live={typing.length > 0}
          avatar={
            group.avatar ? (
              <Avatar name={group.name} src={group.avatar} className="size-10" />
            ) : (
              <div className="flex -space-x-2" aria-hidden>
                {group.members.slice(0, 3).map((m) => (
                  <Avatar key={m.agent_id} name={m.name} src={m.avatar} className="size-8 text-xs ring-2 ring-background" />
                ))}
              </div>
            )
          }
        >
          <Button variant="ghost" size="icon" aria-label="Group info" aria-pressed={infoOpen} onClick={() => setInfoOpen((v) => !v)}>
            <Info />
          </Button>
        </ChatHeader>
        <Messages entries={entries} names={names} avatars={avatars} typing={typing} readByAll={readByAll} onReply={setReplyTo} onReact={react} onEdit={edit} onDelete={remove} />
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
