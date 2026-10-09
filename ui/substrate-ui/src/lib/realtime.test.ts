import { describe, expect, it } from "vitest";
import { FeedClient, splitSse, type FeedEvent } from "./realtime";

const sse = (...events: object[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");

/** A response whose body is `chunks`, arriving one at a time, then either ending or staying open. */
function respond(chunks: string[], { stayOpen = false }: { stayOpen?: boolean } = {}): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      if (!stayOpen) controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

const settle = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

describe("splitSse", () => {
  it("returns whole events and keeps the start of the next one", () => {
    const { events, rest } = splitSse('data: {"type":"ready","chats":[]}\n\ndata: {"type":"res');
    expect(events).toEqual([{ type: "ready", chats: [] }]);
    expect(rest).toBe('data: {"type":"res');
  });

  it("skips keepalive comments and events it cannot read", () => {
    const { events } = splitSse(': keepalive\n\ndata: not json\n\ndata: {"type":"resync"}\n\n');
    expect(events).toEqual([{ type: "resync" }]);
  });

  it("joins an event that arrives in pieces", () => {
    const first = splitSse('data: {"type":"wor');
    const second = splitSse(first.rest + 'king","chat":"g","names":[]}\n\n');
    expect(second.events).toEqual([{ type: "working", chat: "g", names: [] }]);
  });
});

describe("FeedClient", () => {
  it("passes what it hears to its listeners and remembers how far each conversation has come", async () => {
    const heard: FeedEvent[] = [];
    const sent: Record<string, number>[] = [];
    const entry = { seq: 7, text: "hi" } as never;
    const client = new FeedClient(async (since) => {
      sent.push(since);
      return respond([sse({ type: "ready", chats: ["g"] }, { type: "entry", chat: "g", entry })], { stayOpen: true });
    });
    client.subscribe((e) => heard.push(e));
    client.note("g", 3);
    client.start();
    await settle();
    expect(heard.map((e) => e.type)).toEqual(["ready", "entry"]);
    expect(client.connected).toBe(true);
    expect(sent[0]).toEqual({ g: 3 });
    client.stop();
    expect(client.connected).toBe(false);
  });

  it("connects again from where it got to when the stream ends", async () => {
    const sent: Record<string, number>[] = [];
    let calls = 0;
    const client = new FeedClient(async (since) => {
      sent.push(since);
      calls += 1;
      return calls === 1
        ? respond([sse({ type: "ready", chats: ["g"] }, { type: "entry", chat: "g", entry: { seq: 5 } })])
        : respond([sse({ type: "ready", chats: ["g"] })], { stayOpen: true });
    });
    client.start();
    await settle(1300);
    expect(sent).toEqual([{}, { g: 5 }]);
    client.stop();
  });

  it("reconnects at once when the server says it fell behind", async () => {
    let calls = 0;
    const client = new FeedClient(async () => {
      calls += 1;
      return respond([sse({ type: "ready", chats: [] }, { type: "resync" })], { stayOpen: true });
    });
    client.start();
    await settle(1300);
    expect(calls).toBeGreaterThanOrEqual(2);
    client.stop();
  });

  it("stops asking once it is stopped", async () => {
    let calls = 0;
    const client = new FeedClient(async () => {
      calls += 1;
      return respond([], {});
    });
    client.start();
    await settle(10);
    client.stop();
    const after = calls;
    await settle(1300);
    expect(calls).toBe(after);
  });

  it("keeps going when a listener fails", async () => {
    const heard: string[] = [];
    const client = new FeedClient(async () => respond([sse({ type: "ready", chats: [] }, { type: "resync" })], { stayOpen: true }));
    client.subscribe(() => {
      throw new Error("a bad listener");
    });
    client.subscribe((e) => heard.push(e.type));
    client.start();
    await settle(50);
    expect(heard).toContain("ready");
    client.stop();
  });
});
