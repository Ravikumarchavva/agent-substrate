import { describe, expect, it } from "vitest";
import { dayLabel, fileMeta, fileSize, snippetOf, hueOf, mentionChoices, mentionQuery, previewOf, sameDay, splitMentions, typingLine } from "./text";

describe("group text helpers", () => {
  it("gives a name the same colour every time", () => {
    expect(hueOf("Scout")).toBe(hueOf("Scout"));
    expect(hueOf("Scout")).not.toBe(hueOf("Quill"));
  });

  it("marks the mentions in a message and leaves the rest", () => {
    expect(splitMentions("hi @Scout and @everyone!", ["Scout", "Quill"])).toEqual([
      { text: "hi ", mention: false },
      { text: "@Scout", mention: true },
      { text: " and ", mention: false },
      { text: "@everyone", mention: true },
      { text: "!", mention: false },
    ]);
  });

  it("does not take a longer word for a mention", () => {
    expect(splitMentions("@Scouting", ["Scout"])).toEqual([{ text: "@Scouting", mention: false }]);
  });

  it("finds the mention being typed", () => {
    expect(mentionQuery("hello @Sc")).toBe("Sc");
    expect(mentionQuery("@")).toBe("");
    expect(mentionQuery("mail me at a@b")).toBeNull();
    expect(mentionQuery("hello")).toBeNull();
  });

  it("offers everyone and the members that match", () => {
    expect(mentionChoices("", ["Scout", "Quill"])).toEqual(["everyone", "Scout", "Quill"]);
    expect(mentionChoices("sc", ["Scout", "Quill"])).toEqual(["Scout"]);
  });

  it("says who is typing", () => {
    expect(typingLine([])).toBe("");
    expect(typingLine(["Scout"])).toBe("Scout is typing…");
    expect(typingLine(["Scout", "Quill", "Max"])).toBe("Scout, Quill and Max are typing…");
  });

  it("labels days the way a chat does", () => {
    const now = new Date(2026, 9, 5, 15, 0);
    expect(dayLabel(new Date(2026, 9, 5, 9, 0).toISOString(), now)).toBe("Today");
    expect(dayLabel(new Date(2026, 9, 4, 23, 0).toISOString(), now)).toBe("Yesterday");
    expect(dayLabel(new Date(2026, 9, 1, 9, 0).toISOString(), now)).toMatch(/day$/);
    expect(dayLabel(new Date(2026, 8, 1, 9, 0).toISOString(), now)).toContain("September");
    expect(sameDay(new Date(2026, 9, 5, 1, 0).toISOString(), new Date(2026, 9, 5, 23, 0).toISOString())).toBe(true);
  });

  it("writes file sizes people can read", () => {
    expect(fileSize(512)).toBe("512 B");
    expect(fileSize(1536)).toBe("1.5 KB");
    expect(fileSize(3.2 * 1024 * 1024)).toBe("3.2 MB");
  });

  it("previews a message that is only files", () => {
    expect(previewOf("hello", [])).toBe("hello");
    expect(previewOf("- **Sosakumenkobo** — [Michelin](https://x.test) ramen\n- Iruca", [])).toBe("Sosakumenkobo — Michelin ramen Iruca");
    expect(previewOf("", [{ name: "a.pdf" }])).toBe("📎 a.pdf");
    expect(previewOf("  ", [{ name: "a" }, { name: "b" }])).toBe("📎 2 files");
  });

  it("describes a file card the way a messenger does", () => {
    expect(fileMeta({ name: "Resume.pdf", size: 104 * 1024, pages: 2 })).toBe("2 pages • PDF • 104 KB");
    expect(fileMeta({ name: "notes.txt", size: 122 })).toBe("TXT • 122 B");
    expect(fileMeta({ name: "one.pdf", size: 2048, pages: 1 })).toBe("1 page • PDF • 2.0 KB");
    expect(snippetOf("a\nb\nc\nd", 2)).toBe("a\nb");
  });
});
