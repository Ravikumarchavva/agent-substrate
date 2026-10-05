"use client";

import { useRef, useState } from "react";
import { Send, X } from "lucide-react";
import { Button, Textarea } from "@/design";
import type { GroupEntry } from "@/lib/api/groups";
import { mentionChoices, mentionQuery } from "./text";

type Props = {
  names: string[];
  replyTo: GroupEntry | null;
  onClearReply: () => void;
  onSend: (text: string) => Promise<void>;
};

/** Where you write to the group. Typing `@` offers the members and `@everyone`; Enter sends, Shift+Enter starts a new line. */
export function Composer({ names, replyTo, onClearReply, onSend }: Props) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [busy, setBusy] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);

  const query = mentionQuery(text.slice(0, caret));
  const choices = query === null ? [] : mentionChoices(query, names);

  const pick = (name: string) => {
    const before = text.slice(0, caret).replace(/@[\w.-]*$/, `@${name} `);
    const next = before + text.slice(caret);
    setText(next);
    requestAnimationFrame(() => {
      field.current?.focus();
      field.current?.setSelectionRange(before.length, before.length);
      setCaret(before.length);
    });
  };

  const send = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      await onSend(body);
      setText("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-t border-border bg-background px-4 pb-4 pt-3">
      <div className="relative mx-auto w-full max-w-3xl space-y-2">
        {choices.length > 0 && (
          <ul className="absolute bottom-full left-0 z-10 mb-2 w-56 overflow-hidden rounded-xl border border-border bg-card p-1 shadow-lg">
            {choices.map((name) => (
              <li key={name}>
                <Button variant="ghost" onClick={() => pick(name)} className="h-auto! w-full justify-start px-2.5 py-1.5 text-sm font-normal">
                  @{name}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {replyTo && (
          <div className="flex items-center gap-2 rounded-lg border-l-2 border-accent bg-card px-3 py-1.5 text-xs text-muted">
            <p className="min-w-0 flex-1 truncate">
              Replying to <span className="font-medium text-foreground">{replyTo.sender}</span>: {replyTo.text}
            </p>
            <Button variant="ghost" size="icon-sm" aria-label="Cancel reply" onClick={onClearReply}>
              <X />
            </Button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <Textarea
            ref={field}
            rows={1}
            value={text}
            placeholder="Message the group. @ to address someone."
            onChange={(e) => {
              setText(e.target.value);
              setCaret(e.target.selectionStart);
            }}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (choices.length > 0 && query) pick(choices[0]);
                else void send();
              }
            }}
            className="max-h-40 min-h-control-lg resize-none"
          />
          <Button variant="primary" size="icon" aria-label="Send" disabled={!text.trim() || busy} onClick={() => void send()}>
            <Send />
          </Button>
        </div>
      </div>
    </div>
  );
}
