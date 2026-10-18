import { describe, expect, it } from "vitest";
import { buildViewPath, parseChatPath } from "./chat-routes";

describe("parseChatPath", () => {
  it("reads a conversation, a settings tab, or a page of its own", () => {
    expect(parseChatPath("/")).toEqual({ threadId: null, settingsTab: null, view: null, groupId: null, agentId: null, agentTab: null, observeAgentId: null, observeChat: null });
    expect(parseChatPath("/abc-123")).toEqual({ threadId: "abc-123", settingsTab: null, view: null, groupId: null, agentId: null, agentTab: null, observeAgentId: null, observeChat: null });
    expect(parseChatPath("/settings/memory")).toMatchObject({ threadId: null, settingsTab: "memory" });
    expect(parseChatPath("/scheduled")).toEqual({ threadId: null, settingsTab: null, view: "scheduled", groupId: null, agentId: null, agentTab: null, observeAgentId: null, observeChat: null });
    expect(parseChatPath("/notifications")).toMatchObject({ view: "notifications", threadId: null });
    expect(parseChatPath("/agents")).toEqual({ threadId: null, settingsTab: null, view: "agents", groupId: null, agentId: null, agentTab: null, observeAgentId: null, observeChat: null });
  });

  it("reads the group open on the Groups page", () => {
    expect(parseChatPath("/groups")).toMatchObject({ view: "groups", groupId: null, threadId: null });
    expect(parseChatPath("/groups/g-1")).toMatchObject({ view: "groups", groupId: "g-1" });
    expect(buildViewPath("groups", "g-1")).toBe("/chat/groups/g-1");
  });

  it("reads an agent's chat, its profile, and the form for a new one", () => {
    expect(parseChatPath("/agents/a-1")).toMatchObject({ view: "agents", agentId: "a-1", agentTab: null, groupId: null });
    expect(parseChatPath("/agents/a-1/info")).toMatchObject({ view: "agents", agentId: "a-1", agentTab: "info" });
    expect(parseChatPath("/agents/new")).toMatchObject({ view: "agents", agentId: "new", agentTab: null, observeAgentId: null, observeChat: null });
    expect(parseChatPath("/agents/a-1/nonsense")).toMatchObject({ agentId: "a-1", agentTab: null, observeAgentId: null, observeChat: null });
    expect(parseChatPath("/view/a-1")).toMatchObject({ view: "view", observeAgentId: "a-1", observeChat: null, agentId: null });
    expect(parseChatPath("/view/a-1/pair-b2")).toMatchObject({ view: "view", observeAgentId: "a-1", observeChat: "pair-b2" });
    expect(parseChatPath("/view")).toMatchObject({ view: "view", observeAgentId: null });
    expect(buildViewPath("view", "a-1/pair-b2")).toBe("/chat/view/a-1/pair-b2");
    expect(buildViewPath("agents", "a-1/info")).toBe("/chat/agents/a-1/info");
  });

  it("never mistakes a page name for a conversation id", () => {
    expect(parseChatPath("/scheduled").threadId).toBeNull();
  });

  it("builds the browser path with the mount prefix", () => {
    expect(buildViewPath("scheduled")).toBe("/chat/scheduled");
  });
});
