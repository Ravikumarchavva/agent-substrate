/**
 * One open stream from the server with everything happening in your conversations, kept open for as long as you are here.
 *
 * The stream is a convenience over the record, never the record: it is told where you are (the last entry you hold of each conversation) every time
 * it connects, so a dropped connection, a laptop that slept or a server that restarted costs a moment, not a message. It reconnects by itself,
 * waiting longer after each failure, and at once when the server says it fell behind (`resync`).
 */
import type { GroupEntry } from "@/lib/api/groups";

export type FeedEvent =
  | { type: "ready"; chats: string[] }
  | { type: "entry"; chat: string; entry: GroupEntry }
  | { type: "working"; chat: string; names: string[] }
  | { type: "read"; chat: string; by_all: number }
  | { type: "chats"; chats: string[] }
  | { type: "resync" };

/** Cut what has arrived into whole `data:` events (parsed) and what is left over, which is the start of the next one. Comment lines (keepalives) are skipped. */
export function splitSse(buffer: string): { events: unknown[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const events: unknown[] = [];
  for (const part of parts) {
    const data = part
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) continue;
    try {
      events.push(JSON.parse(data));
    } catch {
      // an unreadable event is skipped; the next catch-up has what it said
    }
  }
  return { events, rest };
}

const FIRST_DELAY_MS = 1000;
const LONGEST_DELAY_MS = 15_000;

/** Wait, but stop waiting at once if the stream is closed. */
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

export class FeedClient {
  private since: Record<string, number> = {};
  private listeners = new Set<(event: FeedEvent) => void>();
  private abort: AbortController | null = null;
  private live = false;

  /** `open` connects once, saying where the client is, and returns the response whose body is the stream. */
  constructor(private readonly open: (since: Record<string, number>, signal: AbortSignal) => Promise<Response>) {}

  get connected(): boolean {
    return this.live;
  }

  subscribe(listener: (event: FeedEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** The client now holds this conversation up to `seq`: the next connection catches up from there. */
  note(chat: string, seq: number): void {
    if (seq > (this.since[chat] ?? -2)) this.since[chat] = seq;
  }

  /** Forget a conversation (it was left or deleted). */
  forget(chat: string): void {
    delete this.since[chat];
  }

  start(): void {
    if (this.abort) return;
    this.abort = new AbortController();
    void this.run(this.abort.signal);
  }

  stop(): void {
    this.abort?.abort();
    this.abort = null;
    this.live = false;
  }

  private emit(event: FeedEvent): void {
    for (const listener of [...this.listeners]) {
      try {
        listener(event);
      } catch (error) {
        console.error("a feed listener failed", error);
      }
    }
  }

  private async run(signal: AbortSignal): Promise<void> {
    let delay = FIRST_DELAY_MS;
    while (!signal.aborted) {
      try {
        const response = await this.open({ ...this.since }, signal);
        if (!response.ok || !response.body) throw new Error(`feed answered ${response.status}`);
        delay = FIRST_DELAY_MS;
        await this.read(response.body.getReader(), signal);
      } catch {
        // it ended or could not start: wait, then connect again from where we are
      }
      this.live = false;
      if (signal.aborted) return;
      await pause(delay, signal);
      delay = Math.min(delay * 2, LONGEST_DELAY_MS);
    }
  }

  private async read(reader: ReadableStreamDefaultReader<Uint8Array>, signal: AbortSignal): Promise<void> {
    const decoder = new TextDecoder();
    let buffer = "";
    const stop = () => void reader.cancel().catch(() => undefined);
    signal.addEventListener("abort", stop, { once: true });
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        buffer += decoder.decode(value, { stream: true });
        const { events, rest } = splitSse(buffer);
        buffer = rest;
        for (const raw of events) {
          const event = raw as FeedEvent;
          if (event.type === "ready") this.live = true;
          if (event.type === "entry") this.note(event.chat, event.entry.seq);
          this.emit(event);
          if (event.type === "resync") {
            stop();
            return;
          }
        }
      }
    } finally {
      signal.removeEventListener("abort", stop);
    }
  }
}
