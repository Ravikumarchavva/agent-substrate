import { nanoid } from "nanoid";
import { Message, ToolCall } from "@/types";
import { getMessageAttachments } from "./_client";

/**
 * Folds a thread's raw wire-event history (GET /threads/{id}/messages) into
 * the same Message[] shape the live SSE stream renders — both are views over
 * the same backend event vocabulary (user.message/text.delta/tool.call/
 * tool.result/input.requested), sourced from the same EventLog.
 *
 * This is NOT the exact same code as the live per-event reducer in
 * page.tsx: the live path additionally receives `turn.completed`, a
 * richer, server-synthesized summary event (finalized text, attachments,
 * risk-tagged tool calls) that is never durably logged, so it can't be
 * replayed from history. This fold reconstructs turns directly from the
 * same raw primitives instead, using the same turn-boundary rule
 * (a text.delta arriving after a tool.result starts a new turn) the
 * server itself used to use to build turn.completed in the first place.
 * Known gap: assistant-generated file attachments mid-turn only ever
 * arrived via turn.completed, so they aren't recoverable here — only
 * user-uploaded attachments (carried on user.message) survive reload.
 *
 * Also filters out scheduled-task "silent monitoring check" turns (an
 * assistant reply whose entire text is exactly "[SILENT_CHECK]"). This used
 * to be a write-time decision (the backend skipped persisting them); now
 * that the EventLog can't be filtered retroactively, it's a display-time
 * content rule instead — same effective behavior.
 */

type WireEvent = { type: string; [key: string]: unknown };

type CardOption = { key: string; label: string; description?: string };

function rebuildHitlCard(output: string, timestamp: Date): Message | null {
  let parsed: {
    status?: string;
    user_choice?: string;
    _card?: {
      request_id?: string;
      question?: string;
      context?: string;
      options?: CardOption[];
      allow_freeform?: boolean;
    };
  };
  try {
    parsed = JSON.parse(output);
  } catch {
    return null;
  }
  const card = parsed._card;
  if (!card) return null;
  const answered = parsed.status === "answered";
  return {
    id: nanoid(),
    role: "human_input",
    content: "",
    timestamp,
    metadata: {
      requestId: card.request_id || nanoid(),
      question: card.question || "",
      context: card.context || "",
      options: card.options || [],
      allowFreeform: card.allow_freeform ?? true,
      initialStatus: answered ? "answered" : "skipped",
      initialAnswerLabel: answered ? parsed.user_choice || "" : "",
    },
  };
}

export function foldWireEventsToMessages(events: WireEvent[]): Message[] {
  const messages: Message[] = [];
  // A plain `let` here confuses TS's control-flow narrowing once it's
  // reassigned from inside a nested function across multiple switch cases
  // (it infers `never` at some read sites) — an object property narrows
  // reliably instead.
  const state: { active: Message | null; sawToolResult: boolean } = {
    active: null,
    sawToolResult: false,
  };

  function flush(): void {
    const bubble = state.active;
    const isSilentCheck = bubble?.content.trim() === "[SILENT_CHECK]";
    if (
      bubble &&
      !isSilentCheck &&
      (bubble.content || (bubble.toolCalls && bubble.toolCalls.length > 0))
    ) {
      messages.push(bubble);
    }
    state.active = null;
    state.sawToolResult = false;
  }

  function ensureActive(): Message {
    if (!state.active) {
      state.active = {
        id: nanoid(),
        role: "assistant",
        content: "",
        timestamp: new Date(),
      };
    }
    return state.active;
  }

  for (const event of events) {
    switch (event.type) {
      case "user.message": {
        flush();
        messages.push({
          id: nanoid(),
          role: "user",
          content: String(event.text ?? ""),
          timestamp: new Date(),
          attachments: getMessageAttachments({ attachments: event.attachments }),
        });
        break;
      }

      case "text.delta": {
        if (state.sawToolResult) flush();
        const bubble = ensureActive();
        bubble.content += String(event.text ?? "");
        break;
      }

      case "reasoning.delta": {
        const bubble = ensureActive();
        bubble.reasoning = (bubble.reasoning ?? "") + String(event.text ?? "");
        break;
      }

      case "tool.call": {
        if (event.tool_name === "manage_tasks" || event.tool_name === "ask_human") break;
        if (state.sawToolResult) flush();
        const bubble = ensureActive();
        const toolCall: ToolCall = {
          id: (event.call_id as string) || nanoid(),
          name: (event.tool_name as string) || "tool",
          arguments: JSON.stringify(event.args || {}),
          risk: event.risk as ToolCall["risk"],
        };
        bubble.toolCalls = [...(bubble.toolCalls ?? []), toolCall];
        break;
      }

      case "tool.result": {
        if (event.tool_name === "manage_tasks") break;
        if (event.tool_name === "ask_human") {
          const output = typeof event.output === "string" ? event.output : "";
          const card = rebuildHitlCard(output, new Date());
          if (card) {
            flush();
            messages.push(card);
          }
          break;
        }
        const resultText = String((event.ok ? event.output : event.error) ?? "");
        const current: Message | null = state.active;
        if (current?.toolCalls) {
          current.toolCalls = current.toolCalls.map((tc: ToolCall) => {
            const matchById = event.call_id && tc.id === event.call_id;
            const matchByName = tc.name === event.tool_name;
            if (!matchById && !matchByName) return tc;
            return { ...tc, result: resultText, isError: !event.ok };
          });
        }
        state.sawToolResult = true;
        break;
      }

      // input.requested (a still-pending, unanswered question) is
      // deliberately not rendered here — a genuinely pending card is
      // restored via GET /hitl/status/{thread_id}, exactly as it always
      // was; rendering it here too would double-render it.
      default:
        break;
    }
  }

  flush();
  return messages;
}
