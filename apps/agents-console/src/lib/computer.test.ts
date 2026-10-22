import { describe, expect, it } from "vitest";
import type { Message } from "@/types";
import { browserSteps, stepsOf, summarise, terminalSteps } from "./computer";

const msg = (toolCalls: Message["toolCalls"]): Message => ({ id: "m1", role: "assistant", content: "", timestamp: new Date(), toolCalls });

describe("computer panel steps", () => {
  it("summarises a command, code, or the first short argument", () => {
    expect(summarise({ id: "1", name: "code_interpreter", arguments: { command: "ls -la\nwc -l x" } })).toEqual({ summary: "ls -la", code: "ls -la\nwc -l x" });
    expect(summarise({ id: "2", name: "web_search", arguments: '{"query":"agent runtimes"}' })).toEqual({ summary: "agent runtimes", code: "" });
    expect(summarise({ id: "3", name: "x", arguments: "not json" })).toEqual({ summary: "", code: "" });
    expect(summarise({ id: "4", name: "ask_agent", arguments: { agent: "Scout", request: "find pricing" } }).summary).toBe("Scout: find pricing");
  });

  it("marks a call running only while the run is, failed when it errored", () => {
    const calls = [
      { id: "a", name: "web_search", arguments: {}, result: "ok" },
      { id: "b", name: "code_interpreter", arguments: { code: "1/0" }, result: "ZeroDivisionError", isError: true },
      { id: "c", name: "read_file", arguments: {} },
    ];
    expect(stepsOf([msg(calls)], true).map((s) => s.state)).toEqual(["done", "failed", "running"]);
    expect(stepsOf([msg(calls)], false).map((s) => s.state)).toEqual(["done", "failed", "done"]);
  });

  it("lists the pages opened and searches run for the browser", () => {
    const steps = stepsOf([msg([
      { id: "a", name: "web_search", arguments: { query: "agent runtimes" } },
      { id: "b", name: "web_surfer", arguments: { url: "https://acme.dev/blog" } },
      { id: "c", name: "calculator", arguments: { expression: "1+1" } },
    ])], false);
    expect(browserSteps(steps).map((s) => s.web)).toEqual([{ kind: "search", target: "agent runtimes" }, { kind: "page", target: "https://acme.dev/blog" }]);
  });

  it("keeps only the steps that ran code for the terminal", () => {
    const steps = stepsOf([msg([{ id: "a", name: "web_search", arguments: {} }, { id: "b", name: "code_interpreter", arguments: { code: "print(1)" } }])], false);
    expect(terminalSteps(steps).map((s) => s.name)).toEqual(["code_interpreter"]);
  });
});
