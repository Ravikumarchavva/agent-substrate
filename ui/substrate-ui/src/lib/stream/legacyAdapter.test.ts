import { describe, it, expect } from "vitest";
import type { WireEvent } from "@/protocol";
import { wireEventToLegacy } from "./legacyAdapter";

describe("wireEventToLegacy", () => {
  it("drops handshake + ping", () => {
    expect(wireEventToLegacy({ type: "protocol.hello", version: "1.0.0" })).toEqual([]);
    expect(wireEventToLegacy({ type: "ping" })).toEqual([]);
  });

  it("maps text + reasoning deltas", () => {
    expect(wireEventToLegacy({ type: "text.delta", text: "hi" })).toEqual([
      { type: "text_delta", content: "hi", partial: true },
    ]);
    expect(wireEventToLegacy({ type: "reasoning.delta", text: "hmm" })).toEqual([
      { type: "reasoning_delta", content: "hmm", partial: true },
    ]);
  });

  it("maps a tool call and its result", () => {
    const call = wireEventToLegacy({
      type: "tool.call", call_id: "c1", tool_name: "web_search", args: { q: "x" }, agent: "researcher", depth: 1,
    });
    expect(call[0]).toMatchObject({ type: "tool_call", tool_name: "web_search", tool_call_id: "c1" });

    const okResult = wireEventToLegacy({
      type: "tool.result", call_id: "c1", tool_name: "web_search", ok: true, agent: "researcher", depth: 1,
    });
    expect(okResult[0]).toMatchObject({ type: "tool_result", tool_call_id: "c1", is_error: false });

    const errResult = wireEventToLegacy({
      type: "tool.result", call_id: "c2", tool_name: "flaky", ok: false, error: "boom", depth: 1,
    });
    expect(errResult[0]).toMatchObject({ type: "tool_result", is_error: true, content: "boom" });
  });

  it("surfaces a handoff as a tool chip", () => {
    const out = wireEventToLegacy({
      type: "agent.handoff", source_agent: "coordinator", target_agent: "researcher", depth: 0,
    });
    expect(out[0]).toMatchObject({ type: "tool_call", tool_name: "→ researcher" });
  });

  it("maps turn.completed to a completion with tool calls", () => {
    const out = wireEventToLegacy({
      type: "turn.completed",
      text: "done",
      tool_calls: [{ id: "t1", name: "calc", args: { e: "1+1" } }],
      attachments: [],
      finish_reason: "stop",
    } as WireEvent);
    expect(out[0]).toMatchObject({ type: "completion", content: "done", has_tool_calls: true });
  });

  it("maps a ui.resource to a ui_resource open/update", () => {
    const out = wireEventToLegacy({
      type: "ui.resource",
      call_id: "c1",
      uri: "ui://kanban_board",
      structured_content: { task_list: { id: "t1", tasks: [] } },
      render: "panel",
      text: "",
    } as WireEvent);
    expect(out[0]).toMatchObject({
      type: "ui_resource",
      uri: "ui://kanban_board",
      render: "panel",
    });
    expect((out[0] as { structured_content: { task_list: unknown } }).structured_content.task_list).toBeTruthy();
  });

  it("maps run lifecycle + HITL", () => {
    expect(wireEventToLegacy({ type: "run.completed" })[0]).toEqual({ type: "agent.run_completed" });
    expect(wireEventToLegacy({ type: "run.failed", error: "x" })[0]).toEqual({ type: "error", error: "x" });
    expect(wireEventToLegacy({ type: "run.cancelled" })[0]).toEqual({ type: "cancelled" });
    expect(
      wireEventToLegacy({ type: "approval.requested", request_id: "r1", tool_name: "send", args: {} })[0]
    ).toMatchObject({ type: "tool_approval_request", request_id: "r1" });
    expect(
      wireEventToLegacy({ type: "input.requested", request_id: "r2", question: "Name?" })[0]
    ).toMatchObject({ type: "human_input_request", question: "Name?" });
  });
});
