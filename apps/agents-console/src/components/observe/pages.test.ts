import { describe, expect, it } from "vitest";
import type { ViewChat } from "@/lib/api/observe";
import { bumped, mergePage } from "./pages";

const row = (key: string, at: string): ViewChat => ({ key, kind: "agent", id: key, name: key, avatar: null, preview: "", last_sender: null, at, count: 1 });

describe("mergePage", () => {
  it("adds the next page after the rows held, newest first, and keeps a repeated row once", () => {
    const held = [row("c", "2026-10-03T00:00:00Z"), row("b", "2026-10-02T00:00:00Z")];
    const next = mergePage(held, [row("b", "2026-10-02T00:00:00Z"), row("a", "2026-10-01T00:00:00Z")]);
    expect(next.map((r) => r.key)).toEqual(["c", "b", "a"]);
  });
});

describe("bumped", () => {
  it("moves a held conversation to the top with its new line, and ignores one that is not held", () => {
    const held = [row("c", "2026-10-03T00:00:00Z"), row("a", "2026-10-01T00:00:00Z")];
    const next = bumped(held, "a", "hello", "Atlas", "2026-10-09T00:00:00Z");
    expect(next.map((r) => r.key)).toEqual(["a", "c"]);
    expect(next[0]).toMatchObject({ preview: "hello", last_sender: "Atlas" });
    expect(bumped(held, "zzz", "x", null, "2026-10-09T00:00:00Z")).toBe(held);
  });
});
