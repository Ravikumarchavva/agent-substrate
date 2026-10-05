import { describe, expect, it } from "vitest";
import { hueOf, mentionChoices, mentionQuery, splitMentions, typingLine } from "./text";

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
});
