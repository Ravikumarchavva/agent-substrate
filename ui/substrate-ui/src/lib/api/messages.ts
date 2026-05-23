import { BackendMessage, Message, ToolCall } from "@/types";
import { getMessageAttachments, isPersistentToolCall, requestJson } from "./_client";

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
        // Parse tool calls from generation (includes _meta for MCP Apps)
        const toolCalls: ToolCall[] | undefined =
          msg.generation?.tool_calls?.map((tc) => ({
            id: tc.id,
            name: tc.name,
            arguments: tc.arguments,
            result: "Completed",
            _meta: tc._meta || undefined,
          })).filter(isPersistentToolCall);

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
