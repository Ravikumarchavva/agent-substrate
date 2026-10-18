"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";
import { Badge, Button } from "@/design";
import { api } from "@/lib/api";
import type { DocumentCard, DocumentDetail } from "@/lib/api/files";

const POLL_MS = 3000;

/**
 * What the assistant knows a document is about: the card a model wrote when the file was uploaded, and where it is filed. Marked as
 * auto-written because it can be wrong; the file itself is what the assistant quotes from. Renders nothing for a file that has no card.
 */
export function DocumentSummary({ threadId, fileName }: { threadId: string; fileName: string }) {
  const [card, setCard] = useState<DocumentCard | null>(null);
  const [asked, setAsked] = useState(false);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);

  const load = useCallback(async () => {
    const cards = await api.getThreadDocuments(threadId);
    setCard(cards.find((c) => c.name === fileName) ?? null);
  }, [threadId, fileName]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads from the server when the file changes
    void load();
  }, [load]);

  const fileId = card?.file_id ?? null;
  const cardState = card?.state;
  useEffect(() => {
    if (!fileId) return;
    let cancelled = false;
    // refetched when the description finishes, so the section list gains its descriptions
    api.getFileDocument(fileId).then((d) => !cancelled && setDetail(d));
    return () => {
      cancelled = true;
    };
  }, [fileId, cardState]);

  const running = card?.state === "running" || asked;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the request is over once the server says so
    if (asked && card && card.state !== "running") setAsked(false);
  }, [asked, card]);

  if (!card) return null;

  const describeAgain = async () => {
    if (!card.file_id) return;
    setAsked(true);
    try {
      await api.describeFileAgain(card.file_id);
    } catch {
      setAsked(false);
    }
  };

  return (
    <div className="mx-5 mt-4 flex flex-col gap-2 rounded-xl border border-border bg-background/40 px-4 py-3">
      <div className="flex items-center gap-2">
        <Sparkles className="size-3.5 text-accent" />
        <span className="text-xs font-semibold text-foreground">What the assistant knows about this file</span>
        <Badge tone="neutral">Auto-written</Badge>
        {card.topics.map((t) => (
          <Badge key={t} tone="accent">
            {t}
          </Badge>
        ))}
        <div className="ml-auto">
          {card.file_id && (
            <Button size="sm" variant="ghost" disabled={running} onClick={() => void describeAgain()}>
              <RefreshCw className={running ? "animate-spin" : ""} />
              {running ? "Writing…" : card.description ? "Write again" : "Write summary"}
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs leading-relaxed text-muted">
        {card.description ??
          (card.state === "failed"
            ? "The summary could not be written. The assistant can still read the file."
            : running
              ? "Writing a summary…"
              : "No summary yet. The assistant can still read the file.")}
      </p>
      {detail && detail.sections.length > 1 && (
        <details className="group mt-1">
          <summary className="cursor-pointer text-xs font-medium text-muted hover:text-foreground">
            {detail.sections.length} sections · {detail.pages} page{detail.pages === 1 ? "" : "s"}
          </summary>
          <ol className="mt-2 space-y-2">
            {detail.sections.map((s) => (
              <li key={s.position} className="text-xs">
                <span className="font-medium text-foreground">{s.title}</span>
                <span className="text-muted"> · {s.first_page === s.last_page ? `p. ${s.first_page}` : `pp. ${s.first_page}–${s.last_page}`}</span>
                {s.description && <p className="mt-0.5 text-muted">{s.description}</p>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
