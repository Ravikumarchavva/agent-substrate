import { describe, expect, it } from "vitest";
import type { Agent } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { buildChatItems, filterChatItems, isOpenChat, totalUnread } from "./items";

const agent = (id: string, name: string, last_active: string | null, extra: Partial<Agent> = {}): Agent => ({
  id, name, role: "Role", instructions: "", allowed_tools: null, workspace_id: `dot-${id}`, created_at: "2026-01-01T00:00:00Z",
  thread_id: null, last_active, last_message: null, working: false, avatar: null, pinned_at: null, model: null, sees: null, ...extra,
});
const group = (id: string, name: string, updated_at: string, extra: Partial<Group> = {}): Group => ({
  id, name, created_at: updated_at, updated_at, members: [{ agent_id: "a", name: "Scout", role: "", mode: "all", avatar: null, tokens_used: 0, cost_usd: 0 }],
  last_message: null, last_sender: null, unread: 0, paused: false, working: [], tokens_used: 0, token_cap: 1, budget_usd: null, cost_usd: 0, breaker: 40, avatar: null, pinned_at: null, ...extra,
});

describe("chat list", () => {
  const agents = [agent("a1", "Scout", "2026-10-05T10:00:00Z"), agent("a2", "Quill", "2026-10-05T12:00:00Z", { working: true })];
  const groups = [group("g1", "Trip", "2026-10-05T11:00:00Z", { unread: 3, last_message: "ok", last_sender: "Max" })];

  it("lists the most recent first and pinned ones above that, in the order they were pinned", () => {
    expect(buildChatItems(agents, groups).map((i) => i.name)).toEqual(["Quill", "Trip", "Scout"]);
    const pinned = [agent("a1", "Scout", "2026-10-05T10:00:00Z", { pinned_at: "2026-10-06T09:00:00Z" }), agents[1]];
    expect(buildChatItems(pinned, groups).map((i) => i.name)).toEqual(["Scout", "Quill", "Trip"]);
    const both = [pinned[0], agent("a2", "Quill", "2026-10-05T12:00:00Z", { pinned_at: "2026-10-06T08:00:00Z" })];
    expect(buildChatItems(both, groups).map((i) => i.name)).toEqual(["Quill", "Scout", "Trip"]);
  });

  it("carries each chat's picture", () => {
    const items = buildChatItems([agent("a1", "Scout", null, { avatar: "k1" })], [group("g1", "Trip", "2026-10-05T11:00:00Z", { avatar: "k2" })]);
    expect(Object.fromEntries(items.map((i) => [i.name, i.avatar]))).toEqual({ Scout: "k1", Trip: "k2" });
  });

  it("shows who said the last thing in a group, and typing for a working agent", () => {
    const items = buildChatItems(agents, groups);
    expect(items.find((i) => i.id === "g1")?.preview).toBe("Max: ok");
    expect(items.find((i) => i.id === "a2")?.typing).toEqual(["Quill"]);
  });

  it("filters by kind, unread and search", () => {
    const items = buildChatItems(agents, groups);
    expect(filterChatItems(items, "groups", "").map((i) => i.name)).toEqual(["Trip"]);
    expect(filterChatItems(items, "agents", "").map((i) => i.name)).toEqual(["Quill", "Scout"]);
    expect(filterChatItems(items, "unread", "").map((i) => i.name)).toEqual(["Trip"]);
    expect(filterChatItems(items, "all", "sco").map((i) => i.name)).toEqual(["Scout"]);
    expect(totalUnread(groups)).toBe(3);
  });
});

describe("which row is open", () => {
  it("is the agent or the group the address names, and never one of the other kind with the same id", () => {
    const open = { agentId: "x", groupId: null };
    expect(isOpenChat({ kind: "agent", id: "x" }, open)).toBe(true);
    expect(isOpenChat({ kind: "group", id: "x" }, open)).toBe(false);
    expect(isOpenChat({ kind: "agent", id: "y" }, open)).toBe(false);
    expect(isOpenChat({ kind: "group", id: "g" }, { agentId: null, groupId: "g" })).toBe(true);
  });
});
