import { Message } from "@/types";
import { requestJson } from "./_client";
import { foldWireEventsToMessages } from "./history-fold";

type WireEvent = { type: string; [key: string]: unknown };

export const messageApi = {
  async getMessages(threadId: string): Promise<Message[]> {
    // Backend serves the thread's full conversation as raw wire events,
    // projected directly from the EventLog (single source of truth) — the
    // same event vocabulary a live SSE stream sends. See history-fold.ts
    // for how this is folded into displayed messages.
    const events = await requestJson<WireEvent[]>(`/threads/${threadId}/messages`);
    return foldWireEventsToMessages(events);
  },
};
