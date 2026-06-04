/**
 * Adapt the new wire protocol to the UI's existing internal event shapes.
 *
 * The engine now speaks the clean, versioned protocol (`@/protocol`). The
 * polished chat UI (`page.tsx` + MessageBubble/Sidebar/cards) consumes its own
 * legacy event dicts via `processEvent`. Rather than rewrite that good UI, this
 * is the SINGLE typed translation point between the two — replacing the old
 * scattered three-hop drift with one well-defined adapter.
 *
 * One wire event may fan out to zero or more legacy events.
 */
import type { WireEvent } from "@/protocol";

type Legacy = Record<string, unknown>;

export function wireEventToLegacy(event: WireEvent): Legacy[] {
  switch (event.type) {
    case "protocol.hello":
    case "ping":
      return [];

    case "text.delta":
      return [{ type: "text_delta", content: event.text ?? "", partial: true }];

    case "reasoning.delta":
      return [{ type: "reasoning_delta", content: event.text ?? "", partial: true }];

    case "agent.handoff":
      // Surface delegation as a tool chip so the orchestrator → specialist
      // structure is visible in the existing UI.
      return [
        {
          type: "tool_call",
          tool_name: `→ ${event.target_agent}`,
          tool_call_id: "",
          arguments: event.reason ? { reason: event.reason } : {},
        },
      ];

    case "tool.call":
      return [
        {
          type: "tool_call",
          tool_name: event.tool_name,
          tool_call_id: event.call_id ?? "",
          arguments: event.args ?? {},
          risk: event.risk ?? undefined,
        },
      ];

    case "tool.result":
      return [
        {
          type: "tool_result",
          tool_name: event.tool_name,
          tool_call_id: event.call_id ?? "",
          content: event.ok ? "" : event.error ?? "error",
          is_error: !event.ok,
        },
      ];

    case "turn.completed":
      return [
        {
          type: "completion",
          role: "assistant",
          content: event.text ?? "",
          finish_reason: event.finish_reason ?? "stop",
          has_tool_calls: (event.tool_calls?.length ?? 0) > 0,
          tool_calls: (event.tool_calls ?? []).map((tc) => ({
            id: tc.id,
            name: tc.name,
            arguments: tc.args ?? {},
            risk: tc.risk ?? undefined,
          })),
          attachments: event.attachments ?? [],
        },
      ];

    case "run.completed":
      return [{ type: "agent.run_completed" }];

    case "run.failed":
      return [{ type: "error", error: event.error ?? "Run failed." }];

    case "run.cancelled":
      return [{ type: "cancelled" }];

    case "approval.requested":
      return [
        {
          type: "tool_approval_request",
          request_id: event.request_id,
          tool_name: event.tool_name,
          arguments: event.args ?? {},
        },
      ];

    case "input.requested":
      return [
        {
          type: "human_input_request",
          request_id: event.request_id,
          question: event.prompt,
          options: event.options ?? undefined,
          allow_freeform: !event.options?.length,
        },
      ];

    case "task.created":
      return [{ type: "task_list_created", task_list: event.task_list }];
    case "task.updated":
      return [{ type: "task_updated", task: event.task }];
    case "task.added":
      return [{ type: "task_added", task: event.task }];
    case "task.deleted":
      return [{ type: "task_deleted", task_id: event.task_id }];

    case "error":
      return [{ type: "error", error: event.message }];

    default:
      return [];
  }
}
