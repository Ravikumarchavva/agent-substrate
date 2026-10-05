import { describe, expect, it } from "vitest";
import type { Agent } from "@/lib/api/agents";
import type { Group } from "@/lib/api/groups";
import { buildChatItems, filterChatItems, totalUnread } from "./items";

const agent = (id: string, name: string, last_active: string | null, extra: Partial<Agent> = {}): Agent => ({
  id, name, role: "Role", instructions: "", allowed_tools: null, workspace_id: `dot-${id}`, created_at: "2026-01-01T00:00:00Z",
  thread_id: null, last_active, last_message: null, working: false, ...extra,
});
const group = (id: string, name: string, updated_at: string, extra: Partial<Group> = {}): Group => ({
  id, name, created_at: updated_at, updated_at, members: [{ agent_id: "a", name: "Scout", role: "", mode: "all" }],
  last_message: null, last_sender: null, unread: 0, paused: false, working: [], tokens_used: 0, token_cap: 1, ...extra,
});

describe("chat list", () => {
  const agents = [agent("a1", "Scout", "2026-10-05T10:00:00Z"), agent("a2", "Quill", "2026-10-05T12:00:00Z", { working: true })];
  const groups = [group("g1", "Trip", "2026-10-05T11:00:00Z", { unread: 3, last_message: "ok", last_sender: "Max" })];

  it("lists the most recent first and pinned ones above that", () => {
    expect(buildChatItems(agents, groups, []).map((i) => i.name)).toEqual(["Quill", "Trip", "Scout"]);
    expect(buildChatItems(agents, groups, ["agent:a1"]).map((i) => i.name)).toEqual(["Scout", "Quill", "Trip"]);
  });

  it("shows who said the last thing in a group, and typing for a working agent", () => {
    const items = buildChatItems(agents, groups, []);
    expect(items.find((i) => i.id === "g1")?.preview).toBe("Max: ok");
    expect(items.find((i) => i.id === "a2")?.typing).toEqual(["Quill"]);
  });

  it("filters by kind, unread and search", () => {
    const items = buildChatItems(agents, groups, []);
    expect(filterChatItems(items, "groups", "").map((i) => i.name)).toEqual(["Trip"]);
    expect(filterChatItems(items, "agents", "").map((i) => i.name)).toEqual(["Quill", "Scout"]);
    expect(filterChatItems(items, "unread", "").map((i) => i.name)).toEqual(["Trip"]);
    expect(filterChatItems(items, "all", "sco").map((i) => i.name)).toEqual(["Scout"]);
    expect(totalUnread(groups)).toBe(3);
  });
});
