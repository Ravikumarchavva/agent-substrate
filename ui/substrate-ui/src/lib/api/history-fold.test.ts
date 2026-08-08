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

  it("hides a scheduled-task silent monitoring check from chat history", () => {
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "check the weather every hour" },
      { type: "text.delta", text: "[SILENT_CHECK]" },
    ]);

    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe("user");
  });

  it("does not hide an assistant reply that merely mentions SILENT_CHECK", () => {
    const messages = foldWireEventsToMessages([
      { type: "user.message", text: "what does silent check mean?" },
      { type: "text.delta", text: "[SILENT_CHECK] is an internal marker, not user text." },
    ]);

    expect(messages).toHaveLength(2);
  });

  // Citations — the reload-survival guarantee. structured_content is
  // logged durably (see agent-substrate's runtime/context/tool.py), so a
  // page reload replays these same wire events; this fold must reconstruct
  // the same `sources` a live SSE session would have attached.
  describe("citations", () => {
    const citationEvent = (index: number, fileName: string) => ({
      type: "tool.result",
      call_id: `c${index}`,
      tool_name: "knowledge_search",
      ok: true,
      output: `[${index}] (${fileName}, p.${index})`,
      structured_content: {
        citations: [{ index, file_name: fileName, page: index }],
      },
    });

    it("carries citations onto the bubble started after the tool.result flush", () => {
      // This is the divergence the live reducer doesn't have: tool.result
      // sets sawToolResult, so the NEXT text.delta starts a brand-new
      // bubble (see ensureActive/flush above) — that new bubble must still
      // inherit the sources gathered so far, or reload silently drops them.
      const messages = foldWireEventsToMessages([
        { type: "user.message", text: "who signed it?" },
        { type: "tool.call", call_id: "c1", tool_name: "knowledge_search", args: {} },
        citationEvent(1, "doc.pdf"),
        { type: "text.delta", text: "Dr. Shanthi signed it [1]." },
      ]);

      const answer = messages.find(
        (m) => m.role === "assistant" && m.content.includes("Dr. Shanthi")
      );
      expect(answer?.sources).toEqual([
        { index: 1, fileName: "doc.pdf", page: 1 },
      ]);
    });

    it("accumulates citations across multiple knowledge_search calls in one turn", () => {
      const messages = foldWireEventsToMessages([
        { type: "user.message", text: "summarize this document" },
        { type: "tool.call", call_id: "c1", tool_name: "knowledge_search", args: {} },
        citationEvent(1, "doc.pdf"),
        { type: "tool.call", call_id: "c2", tool_name: "knowledge_search", args: {} },
        citationEvent(2, "doc.pdf"),
        { type: "text.delta", text: "Section one [1] and section two [2]." },
      ]);

      const answer = messages.find((m) => m.role === "assistant" && m.content);
      expect(answer?.sources).toHaveLength(2);
      expect(answer?.sources?.map((s) => s.index)).toEqual([1, 2]);
    });

    it("resets pending sources on a new user turn", () => {
      const messages = foldWireEventsToMessages([
        { type: "user.message", text: "first question" },
        { type: "tool.call", call_id: "c1", tool_name: "knowledge_search", args: {} },
        citationEvent(1, "doc.pdf"),
        { type: "text.delta", text: "Answer one [1]." },
        { type: "user.message", text: "second question, unrelated" },
        { type: "text.delta", text: "Answer two, no citations." },
      ]);

      const turnOneAnswer = messages.find((m) => m.content === "Answer one [1].");
      const turnTwoAnswer = messages.find(
        (m) => m.content === "Answer two, no citations."
      );
      expect(turnOneAnswer?.sources).toHaveLength(1);
      expect(turnTwoAnswer?.sources).toBeUndefined();
    });

    it("leaves sources undefined for a turn with no citations", () => {
      const messages = foldWireEventsToMessages([
        { type: "user.message", text: "hi" },
        { type: "text.delta", text: "hello" },
      ]);

      expect(messages[1].sources).toBeUndefined();
    });
  });
});
