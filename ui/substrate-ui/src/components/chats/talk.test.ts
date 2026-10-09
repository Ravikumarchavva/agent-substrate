import { describe, expect, it } from "vitest";
import type { Exchange } from "@/lib/api/agents";
import { counterparts, talkEntries } from "./talk";

const ex = (over: Partial<Exchange>): Exchange => ({
  thread_id: "t1",
  asker_id: "relay",
  asker: "Relay",
  target_id: "atlas",
  target: "Atlas",
  request: "Vault code?",
  answer: "7421-KITE",
  status: "done",
  at: "2026-10-09T23:29:00Z",
  ...over,
});

describe("counterparts", () => {
  it("names each other agent once, whichever way the asking went, most recent first", () => {
    const list = [ex({ thread_id: "a" }), ex({ thread_id: "b", asker_id: "atlas", asker: "Atlas", target_id: "relay", target: "Relay", at: "2026-10-10T08:00:00Z" }), ex({ thread_id: "c", target_id: "quill", target: "Quill", at: "2026-10-11T08:00:00Z" })];
    expect(counterparts("relay", list)).toEqual([
      { id: "quill", name: "Quill", count: 1, last: "2026-10-11T08:00:00Z" },
      { id: "atlas", name: "Atlas", count: 2, last: "2026-10-10T08:00:00Z" },
    ]);
    expect(counterparts("atlas", list).map((c) => c.name)).toEqual(["Relay"]);
  });

  it("does not count a plain chat, which is you", () => {
    expect(counterparts("atlas", [ex({ asker_id: null, asker: "A plain chat" })])).toEqual([]);
  });
});

describe("talkEntries", () => {
  it("is the request then the answer for each time, oldest first, with the viewed agent on its own side", () => {
    const list = [ex({ thread_id: "late", request: "Where?", answer: "Lisbon", at: "2026-10-10T08:00:00Z" }), ex({ thread_id: "early" })];
    const seen = talkEntries("relay", "atlas", list);
    expect(seen.map((e) => [e.sender, e.text, e.from_user])).toEqual([
      ["Relay", "Vault code?", true],
      ["Atlas", "7421-KITE", false],
      ["Relay", "Where?", true],
      ["Atlas", "Lisbon", false],
    ]);
    expect(seen.map((e) => e.seq)).toEqual([0, 1, 2, 3]);
    expect(talkEntries("atlas", "relay", list).map((e) => e.from_user)).toEqual([false, true, false, true]);
  });

  it("says where it stands when there is no answer yet", () => {
    const seen = talkEntries("relay", "atlas", [ex({ answer: null, status: "working" }), ex({ thread_id: "w", answer: null, status: "waiting", at: "2026-10-10T00:00:00Z" })]);
    expect(seen.map((e) => e.text)).toEqual(["Vault code?", "Working on it…", "Vault code?", "Stopped to wait for you to answer something."]);
  });

  it("leaves out what was between other agents", () => {
    expect(talkEntries("relay", "atlas", [ex({ target_id: "quill", target: "Quill" })])).toEqual([]);
  });
});
