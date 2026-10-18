import { describe, expect, it } from "vitest";
import { toSpeechText } from "./speech-text";

describe("toSpeechText", () => {
  it("removes markdown syntax but keeps the words", () => {
    const md = "## Title\n\nApple's **India revenue** was *Rs 67,121 crore* [1]. See [the filing](https://x.test).\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nprint(1)\n```";
    const out = toSpeechText(md);
    expect(out).not.toMatch(/[*#`|\[\]]/);
    expect(out).toContain("Apple's India revenue was Rs 67,121 crore");
    expect(out).toContain("See the filing");
    expect(out).not.toContain("print");
  });
});
