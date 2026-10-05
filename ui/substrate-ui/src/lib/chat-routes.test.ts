import { describe, expect, it } from "vitest";
import { buildViewPath, parseChatPath } from "./chat-routes";

describe("parseChatPath", () => {
  it("reads a conversation, a settings tab, or a page of its own", () => {
    expect(parseChatPath("/")).toEqual({ threadId: null, settingsTab: null, view: null, groupId: null, agentId: null });
    expect(parseChatPath("/abc-123")).toEqual({ threadId: "abc-123", settingsTab: null, view: null, groupId: null, agentId: null });
    expect(parseChatPath("/settings/memory")).toMatchObject({ threadId: null, settingsTab: "memory" });
    expect(parseChatPath("/scheduled")).toEqual({ threadId: null, settingsTab: null, view: "scheduled", groupId: null, agentId: null });
    expect(parseChatPath("/notifications")).toMatchObject({ view: "notifications", threadId: null });
    expect(parseChatPath("/agents")).toEqual({ threadId: null, settingsTab: null, view: "agents", groupId: null, agentId: null });
  });

  it("reads the group open on the Groups page", () => {
    expect(parseChatPath("/groups")).toMatchObject({ view: "groups", groupId: null, threadId: null });
    expect(parseChatPath("/groups/g-1")).toMatchObject({ view: "groups", groupId: "g-1" });
    expect(buildViewPath("groups", "g-1")).toBe("/chat/groups/g-1");
    expect(parseChatPath("/agents/a-1")).toMatchObject({ view: "agents", agentId: "a-1", groupId: null });
  });

  it("never mistakes a page name for a conversation id", () => {
    expect(parseChatPath("/scheduled").threadId).toBeNull();
  });

  it("builds the browser path with the mount prefix", () => {
    expect(buildViewPath("scheduled")).toBe("/chat/scheduled");
  });
});
