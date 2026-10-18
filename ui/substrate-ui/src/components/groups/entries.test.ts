import { describe, expect, it } from "vitest";
import type { GroupEntry } from "@/lib/api/groups";
import { foldEntries, groupAfterEntry } from "./entries";
import type { Group } from "@/lib/api/groups";

const entry = (seq: number, over: Partial<GroupEntry> = {}): GroupEntry => ({
  seq,
  id: `e${seq}`,
  sender_id: "member/scout@g",
  sender: "Scout",
  from_user: false,
  kind: "message",
  text: `m${seq}`,
  mentions: [],
  reply_to: null,
  attachments: [],
  at: "2026-10-09T10:00:00Z",
  ...over,
});
const me = { sender_id: "user/u", sender: "You", from_user: true };

describe("foldEntries", () => {
  it("adds new messages in order and ignores one it already has", () => {
    const once = foldEntries([entry(0)], [entry(2), entry(1)]);
    expect(once.map((e) => e.seq)).toEqual([0, 1, 2]);
    expect(foldEntries(once, [entry(1, { text: "again" })])).toEqual(once);
  });

  it("applies an edit to its message and leaves the marker out of the list", () => {
    const out = foldEntries([entry(0, { ...me })], [entry(1, { ...me, kind: "edit", reply_to: 0, text: "lunch at 2", at: "2026-10-09T10:05:00Z" })]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ text: "lunch at 2", edited_at: "2026-10-09T10:05:00Z" });
  });

  it("keeps one reaction per person, replaces it, and removes it when it is taken back", () => {
    const react = (seq: number, who: typeof me, emoji: string) => entry(seq, { ...who, kind: "reaction", reply_to: 0, text: emoji });
    const scout = { sender_id: "member/scout@g", sender: "Scout", from_user: false };
    let out = foldEntries([entry(0)], [react(1, me, "👍"), react(2, scout, "👍")]);
    expect(out[0].reactions?.map((r) => [r.sender, r.emoji])).toEqual([["You", "👍"], ["Scout", "👍"]]);
    out = foldEntries(out, [react(3, me, "❤️")]);
    expect(out[0].reactions?.find((r) => r.from_user)?.emoji).toBe("❤️");
    out = foldEntries(out, [react(4, me, "")]);
    expect(out[0].reactions?.map((r) => r.sender)).toEqual(["Scout"]);
  });

  it("blanks a deleted message, its files and its reactions", () => {
    const sent = entry(0, { ...me, text: "secret", attachments: [{ name: "a.pdf", size: 1, mime: "application/pdf", key: "k" }], reactions: [{ emoji: "👍", sender_id: "x", sender: "X", from_user: false }] });
    const out = foldEntries([sent], [entry(1, { ...me, kind: "tombstone", reply_to: 0, text: "", at: "2026-10-09T10:09:00Z" })]);
    expect(out[0]).toMatchObject({ text: "", attachments: [], reactions: [], deleted_at: "2026-10-09T10:09:00Z" });
  });

  it("reaches the same place whether history arrives already folded or as raw markers on top of it", () => {
    const markers = [entry(1, { ...me, kind: "edit", reply_to: 0, text: "v2" }), entry(2, { ...me, kind: "edit", reply_to: 0, text: "v3" })];
    const folded = entry(0, { ...me, text: "v3", edited_at: "2026-10-09T10:05:00Z" });
    expect(foldEntries([], [folded, ...markers])[0].text).toBe("v3");
    expect(foldEntries([], [entry(0, { ...me, text: "v1" }), ...markers])[0].text).toBe("v3");
  });

  it("ignores a marker whose message is not here", () => {
    expect(foldEntries([entry(0)], [entry(5, { kind: "edit", reply_to: 99, text: "x" })])).toHaveLength(1);
  });
});

describe("groupAfterEntry", () => {
  const group = { id: "g", last_message: "old", last_sender: "Quill", unread: 1, paused: true, updated_at: "2026-10-01T00:00:00Z" } as Group;

  it("shows the new latest message and counts it unread when someone else said it and you are not in the conversation", () => {
    const next = groupAfterEntry(group, entry(4, { text: "Tokyo it is", sender: "Scout" }), false);
    expect(next).toMatchObject({ last_message: "Tokyo it is", last_sender: "Scout", unread: 2, paused: false, updated_at: "2026-10-09T10:00:00Z" });
  });

  it("does not count it while you are reading, or when you wrote it, or when it is only a note", () => {
    expect(groupAfterEntry(group, entry(4), true).unread).toBe(1);
    expect(groupAfterEntry(group, entry(4, { ...me }), false)).toMatchObject({ unread: 1, last_sender: "You" });
    expect(groupAfterEntry(group, entry(4, { kind: "system", text: "Paused" }), false)).toMatchObject({ unread: 1, paused: true, last_message: "Paused" });
  });

  it("leaves the row alone for an edit, a reaction or a delete", () => {
    expect(groupAfterEntry(group, entry(4, { kind: "reaction", reply_to: 0, text: "👍" }), false)).toBe(group);
  });

  it("shows a file as its name when there was no text", () => {
    const next = groupAfterEntry(group, entry(4, { text: "", attachments: [{ name: "budget.xlsx", size: 1, mime: "x", key: "k" }] }), false);
    expect(next.last_message).toBe("📎 budget.xlsx");
  });
});
