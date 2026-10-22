import type { Message, ToolCall } from "@/types";

/** One thing the assistant did, in the order it did it. */
export interface Step {
  id: string;
  name: string;
  /** A short human line about what it was given ("5 files", a command, a query). */
  summary: string;
  state: "running" | "done" | "failed";
  risk?: ToolCall["risk"];
  /** What came back, for the terminal view. */
  output: string;
  /** The code or command, when the tool ran one. */
  code: string;
  /** The page it opened, or the search it ran, when it used the web. */
  web: { kind: "page" | "search"; target: string } | null;
}

const RUNS_CODE = /^(code_interpreter|shell|bash|terminal|python)/i;

const READS_PAGE = /^(web_surfer|read_url|browse|fetch_url|open_url)/i;
const SEARCHES = /^(web_search|search)/i;

const asObject = (args: ToolCall["arguments"]): Record<string, unknown> => {
  if (typeof args !== "string") return args ?? {};
  try {
    const parsed = JSON.parse(args);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
};

const clip = (text: string, max: number) => (text.length > max ? `${text.slice(0, max)}…` : text);

/** What a tool call looked like, from its arguments: a command or code if it ran one, else the first short value. */
export function summarise(call: ToolCall): { summary: string; code: string } {
  const args = asObject(call.arguments);
  if (call.name === "ask_agent" && args.agent) return { summary: clip(`${args.agent}: ${String(args.request ?? "")}`, 90), code: "" };
  const code = String(args.command ?? args.code ?? "");
  if (code) return { summary: clip(code.split("\n")[0], 90), code };
  const first = Object.values(args).find((v) => typeof v === "string" && v) as string | undefined;
  return { summary: first ? clip(first, 90) : "", code: "" };
}

/** The page a tool call opened or the search it ran, if it used the web. */
export function webOf(call: ToolCall): Step["web"] {
  const args = asObject(call.arguments);
  const url = String(args.url ?? (Array.isArray(args.urls) ? args.urls[0] : "") ?? "");
  if (READS_PAGE.test(call.name) && url) return { kind: "page", target: url };
  const query = String(args.query ?? args.q ?? "");
  if (SEARCHES.test(call.name) && query) return { kind: "search", target: query };
  return null;
}

/** The assistant's tool calls across the conversation, oldest first. A call with no result yet is still running while `running` is true. */
export function stepsOf(messages: Message[], running: boolean): Step[] {
  const out: Step[] = [];
  for (const m of messages) {
    for (const call of m.toolCalls ?? []) {
      const { summary, code } = summarise(call);
      const finished = call.result !== undefined;
      out.push({
        id: `${m.id}:${call.id}`,
        name: call.name,
        summary,
        code,
        web: webOf(call),
        output: call.result ?? "",
        risk: call.risk,
        state: call.isError ? "failed" : finished ? "done" : running ? "running" : "done",
      });
    }
  }
  return out;
}

/** The steps that used the web, newest last: what a browser history would show. */
export const browserSteps = (steps: Step[]): Step[] => steps.filter((s) => s.web !== null);

/** The steps that ran code or a command: what a terminal would have shown. */
export const terminalSteps = (steps: Step[]): Step[] => steps.filter((s) => RUNS_CODE.test(s.name) || s.code);
