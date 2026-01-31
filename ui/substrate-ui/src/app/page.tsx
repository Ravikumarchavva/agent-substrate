"use client";

import { useState, useRef, useEffect } from "react";
import { nanoid } from "nanoid";
import { MessageBubble } from "@/components/MessageBubble";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // Auto-scroll to bottom whenever messages change
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  // Append stream chunk while removing overlaps between lastText and chunk
  function appendChunk(lastText: string, chunk: string) {
    if (!chunk) return lastText;
    if (lastText.endsWith(chunk)) return lastText;
    if (chunk.startsWith(lastText)) return chunk;
    // Find largest overlap
    for (let ov = Math.min(lastText.length, chunk.length); ov > 0; ov--) {
      if (lastText.endsWith(chunk.slice(0, ov))) {
        return lastText + chunk.slice(ov);
      }
    }
    return lastText + chunk;
  }

  async function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userMessage: Message = {
      id: nanoid(),
      role: "user",
      content: input,
    };

    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: nextMessages }),
      });

      if (!res.ok) {
        throw new Error("Request failed");
      }

      // If the response is streamed, read it progressively
      if (!res.body) {
        throw new Error("No stream");
      }

      // Create an assistant placeholder message and append it immediately
      const assistantId = nanoid();
      const assistantMessage: Message = {
        id: assistantId,
        role: "assistant",
        content: "",
      };

      setMessages((m) => [...m, assistantMessage]);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by double newlines
          const parts = buffer.split(/\n\n/);
          // Keep the last (possibly partial) part in the buffer
          buffer = parts.pop() || "";

          for (const part of parts) {
            // Each part may contain one or more lines like 'data: {...}'
            const lines = part.split(/\n/);
            for (const line of lines) {
              const prefix = "data: ";
              if (line.startsWith(prefix)) {
                const jsonStr = line.slice(prefix.length);
                try {
                  const payload = JSON.parse(jsonStr);

                  if (payload.error) {
                    setMessages((m) =>
                      m.map((msg) =>
                        msg.id === assistantId
                          ? { ...msg, content: msg.content + "\n\n⚠️ " + payload.error }
                          : msg
                      )
                    );
                    continue;
                  }

                  // Update role if provided
                  if (payload.role) {
                    setMessages((m) =>
                      m.map((msg) => (msg.id === assistantId ? { ...msg, role: payload.role } : msg))
                    );
                  }

                  // Append content chunks safely
                  if (payload.content != null) {
                    const text = String(payload.content);
                    setMessages((m) =>
                      m.map((msg) =>
                        msg.id === assistantId ? { ...msg, content: appendChunk(msg.content, text) } : msg
                      )
                    );
                  }

                  // If the backend indicates completion, mark loading false
                  if (payload._complete) {
                    setLoading(false);
                  }
                } catch (e) {
                  // If JSON parsing fails, append raw line
                  setMessages((m) =>
                    m.map((msg) =>
                      msg.id === assistantId ? { ...msg, content: appendChunk(msg.content, line) } : msg
                    )
                  );
                }
              } else if (line.trim()) {
                // Non-prefixed lines — append raw
                setMessages((m) =>
                  m.map((msg) =>
                    msg.id === assistantId ? { ...msg, content: appendChunk(msg.content, line) } : msg
                  )
                );
              }
            }
          }
        }

        // Stream ended — ensure loading is cleared
        setLoading(false);
      } catch (err) {
        setMessages((m) =>
          m.map((msg) =>
            msg.id === assistantId
              ? { ...msg, content: msg.content + "\n\n⚠️ Stream error." }
              : msg
          )
        );
        setLoading(false);
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          id: nanoid(),
          role: "assistant",
          content: "⚠️ Something went wrong.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex h-screen flex-col">
      <header className="border-b border-zinc-800 p-4 text-lg font-semibold">
        Agent Chat
      </header>

      <div ref={containerRef} className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            role={m.role}
            content={m.content}
          />
        ))}

        {loading && (
          <div className="text-sm text-zinc-400">Thinking…</div>
        )}
      </div>

      <form
        onSubmit={sendMessage}
        className="border-t border-zinc-800 p-4 flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="flex-1 rounded-md bg-zinc-900 px-3 py-2 text-sm outline-none"
          placeholder="Ask something…"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </main>
  );
}
  