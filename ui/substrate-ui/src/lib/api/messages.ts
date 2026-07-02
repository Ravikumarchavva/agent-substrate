import { BackendMessage, Message, ToolCall } from "@/types";
import { getMessageAttachments, isPersistentToolCall, requestJson } from "./_client";

type CardOption = { key: string; label: string; description?: string };

/** Rebuild an answered/skipped HITL card from a persisted ask_human tool_result.
 *  The result JSON embeds the original question + options under ``_card``. */
function rebuildHitlCard(msg: BackendMessage): Message | null {
  const raw = Array.isArray(msg.output) ? msg.output.join("") : msg.output || "";
  let parsed: {
    status?: string;
    user_choice?: string;
    _card?: { request_id?: string; question?: string; context?: string; options?: CardOption[]; allow_freeform?: boolean };
  };
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  const card = parsed._card;
  if (!card) return null;
  const answered = parsed.status === "answered";
  return {
    id: `${msg.id}-hitl`,
    role: "human_input",
    content: "",
    timestamp: new Date(msg.created_at),
    metadata: {
      requestId: card.request_id || msg.id,
      question: card.question || "",
      context: card.context || "",
      options: card.options || [],
      allowFreeform: card.allow_freeform ?? true,
      initialStatus: answered ? "answered" : "skipped",
      initialAnswerLabel: answered ? parsed.user_choice || "" : "",
    },
  };
}

export const messageApi = {
  async getMessages(threadId: string): Promise<Message[]> {
    const backendMessages = await requestJson<BackendMessage[]>(`/threads/${threadId}/messages`);

    const messages: Message[] = [];

    for (const msg of backendMessages) {
      if (msg.type === "user_message") {
        const displayContent =
          typeof msg.metadata?.display_content === "string"
            ? msg.metadata.display_content
            : msg.input || "";
        messages.push({
          id: msg.id,
          role: "user",
          content: displayContent,
          timestamp: new Date(msg.created_at),
          attachments: getMessageAttachments(msg.metadata),
        });
      } else if (msg.type === "assistant_message") {
        // Persisted ToolUseBlock uses call_id/tool_name; normalize to id/name.
        // (ask_human cards are rebuilt from tool_result rows, not from here.)
        const toolCalls: ToolCall[] | undefined = (msg.generation?.tool_calls ?? [])
          .map((tc) => ({
            id: tc.id ?? tc.call_id ?? "",
            name: tc.name ?? tc.tool_name ?? "",
            arguments: tc.arguments,
            result: "Completed",
            _meta: tc._meta || undefined,
          }))
          .filter((tc) => tc.name !== "ask_human")
          .filter(isPersistentToolCall);

        const content = Array.isArray(msg.output)
          ? msg.output.join("")
          : msg.output || "";

        if (!content.trim() && (!toolCalls || toolCalls.length === 0)) {
          continue;
        }

        messages.push({
          id: msg.id,
          role: "assistant",
          content,
          timestamp: new Date(msg.created_at),
          toolCalls: toolCalls && toolCalls.length > 0 ? toolCalls : undefined,
          attachments: getMessageAttachments(msg.metadata),
        });
      } else if (msg.type === "tool_result") {
        // ask_human results carry the original question + options under _card —
        // rebuild the answered HITL card in chronological position.
        if (msg.name === "ask_human") {
          const card = rebuildHitlCard(msg);
          if (card) messages.push(card);
          continue;
        }
        // Only show tool results that DON'T have a companion MCP App
        // (MCP App tools show their UI inline on the assistant message)
        const hasApp = msg.metadata?.has_app === true;
        if (!hasApp && msg.is_error) {
          messages.push({
            id: msg.id,
            role: "tool_result",
            content: (typeof msg.output === "string" ? msg.output : "") || "",
            timestamp: new Date(msg.created_at),
            metadata: {
              toolName: msg.name || "tool",
              isError: msg.is_error || false,
            },
          });
        }
      }
    }

    return messages;
  },
};
