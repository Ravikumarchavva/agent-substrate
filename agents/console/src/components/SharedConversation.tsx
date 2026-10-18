"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Paperclip } from "lucide-react";

export interface SharedConversationData {
  title: string | null;
  messages: { role: "user" | "assistant"; text: string; attachments: string[] }[];
}

/** A conversation someone shared: read-only, no controls, no account needed. */
export function SharedConversation({ data }: { data: SharedConversationData }) {
  return (
    <main className="mx-auto w-full max-w-chat px-4 py-8 sm:px-6">
      <header className="mb-8 border-b border-border pb-4">
        <p className="text-xs font-medium uppercase tracking-wider text-muted">Shared conversation · read only</p>
        <h1 className="mt-1 text-xl font-semibold text-foreground">{data.title || "Conversation"}</h1>
      </header>

      {data.messages.length === 0 ? (
        <p className="text-sm text-muted">This conversation has no messages yet.</p>
      ) : (
        <ol className="space-y-6">
          {data.messages.map((m, i) => (
            <li key={i} className={m.role === "user" ? "flex justify-end" : ""}>
              {m.role === "user" ? (
                <div className="max-w-4xl rounded-2xl bg-card px-4 py-3 text-base leading-relaxed" style={{ boxShadow: "var(--shadow-sm)" }}>
                  {m.attachments.length > 0 && (
                    <p className="mb-1 flex items-center gap-1.5 text-xs text-muted">
                      <Paperclip className="size-3" /> {m.attachments.join(", ")}
                    </p>
                  )}
                  <p className="whitespace-pre-wrap">{m.text}</p>
                </div>
              ) : (
                <div className="prose-chat">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}
