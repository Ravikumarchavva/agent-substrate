import { describe, expect, it } from "vitest";

import { shouldAutoTitle } from "./thread-title";

describe("shouldAutoTitle", () => {
  it("titles a new conversation from its first message", () => {
    expect(shouldAutoTitle(undefined, 0)).toBe(true);
    expect(shouldAutoTitle({ agent_id: null }, 0)).toBe(true);
  });

  it("does not retitle one that already has messages", () => {
    expect(shouldAutoTitle({ agent_id: null }, 3)).toBe(false);
  });

  it("never retitles a conversation with an agent: it is named after the agent", () => {
    expect(shouldAutoTitle({ agent_id: "a1" }, 0)).toBe(false);
  });
});
