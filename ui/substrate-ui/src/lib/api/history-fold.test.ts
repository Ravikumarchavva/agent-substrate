import { describe, expect, it } from "vitest";
import { foldWireEventsToMessages } from "./history-fold";

describe("foldWireEventsToMessages", () => {
  it("folds a plain user turn + streamed answer into two messages", () => {
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "hello", attachments: [] },
      { type: "text.delta", text: "hi " },
      { type: "text.delta", text: "there" },
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({ role: "user", content: "hello" });
    expect(messages[1]).toMatchObject({ role: "assistant", content: "hi there" });
  });

  it("attaches a tool call + result to its own bubble, ahead of the final answer", () => {
    // Matches live streaming exactly (see page.tsx: "Agent will continue
    // after tool execution — next text should go into a new bubble"): the
    // tool-call bubble and the text that follows it are two separate
    // assistant messages, not one merged bubble.
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "calc" },
      {
        type: "tool.call",
        call_id: "c1",
        tool_name: "calculator",
        args: { expression: "1+1" },
        risk: "safe",
      },
      { type: "tool.result", call_id: "c1", tool_name: "calculator", ok: true, output: "2" },
      { type: "text.delta", text: "The answer is 2." },
    ]);

    const assistants = messages.filter((m) => m.role === "assistant");
    expect(assistants).toHaveLength(2);
    expect(assistants[0].toolCalls).toHaveLength(1);
    expect(assistants[0].toolCalls?.[0]).toMatchObject({
      id: "c1",
      name: "calculator",
      result: "2",
      isError: false,
    });
    expect(assistants[1].content).toBe("The answer is 2.");
  });

  it("starts a new bubble after every tool.result, in strict order", () => {
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "two steps" },
      { type: "tool.call", call_id: "c1", tool_name: "step_a", args: {} },
      { type: "tool.result", call_id: "c1", tool_name: "step_a", ok: true, output: "done a" },
      { type: "text.delta", text: "Step A finished." },
      { type: "tool.call", call_id: "c2", tool_name: "step_b", args: {} },
      { type: "tool.result", call_id: "c2", tool_name: "step_b", ok: true, output: "done b" },
      { type: "text.delta", text: "Step B finished." },
    ]);

    const assistants = messages.filter((m) => m.role === "assistant");
    // [tool_a bubble, "Step A finished." + tool_b bubble, "Step B finished."]
    expect(assistants).toHaveLength(3);
    expect(assistants[0].toolCalls?.[0].name).toBe("step_a");
    expect(assistants[1].content).toBe("Step A finished.");
    expect(assistants[1].toolCalls?.[0].name).toBe("step_b");
    expect(assistants[2].content).toBe("Step B finished.");
  });

  it("rebuilds an answered ask_human card from the tool.result output", () => {
    const output = JSON.stringify({
      status: "answered",
      user_choice: "Yes",
      _card: {
        request_id: "req-1",
        question: "Proceed?",
        context: "",
        options: [{ key: "yes", label: "Yes" }],
        allow_freeform: true,
      },
    });
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "do it" },
      { type: "tool.call", call_id: "c1", tool_name: "ask_human", args: {} },
      { type: "tool.result", call_id: "c1", tool_name: "ask_human", ok: true, output },
    ]);

    const card = messages.find((m) => m.role === "human_input");
    expect(card).toBeDefined();
    expect(card?.metadata).toMatchObject({
      requestId: "req-1",
      question: "Proceed?",
      initialStatus: "answered",
      initialAnswerLabel: "Yes",
    });
  });

  it("skips manage_tasks tool calls and results entirely", () => {
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "plan this" },
      { type: "tool.call", call_id: "c1", tool_name: "manage_tasks", args: {} },
      {
        type: "tool.result",
        call_id: "c1",
        tool_name: "manage_tasks",
        ok: true,
        structured_content: { task_list: {} },
      },
      { type: "text.delta", text: "Done." },
    ]);

    const assistant = messages.find((m) => m.role === "assistant");
    expect(assistant?.toolCalls).toBeUndefined();
    expect(assistant?.content).toBe("Done.");
  });

  it("ignores input.requested — pending cards come from /hitl/status instead", () => {
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "ask me something" },
      { type: "input.requested", request_id: "r1", question: "Pick one" },
    ]);

    expect(messages.some((m) => m.role === "human_input")).toBe(false);
  });

  it("spans multiple runs/turns without losing order", () => {
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "first" },
      { type: "text.delta", text: "first answer" },
      { type: "user.message", text: "second" },
      { type: "text.delta", text: "second answer" },
    ]);

    expect(messages.map((m) => m.content)).toEqual([
      "first",
      "first answer",
      "second",
      "second answer",
    ]);
  });
});
