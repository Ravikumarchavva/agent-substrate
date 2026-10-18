"use client";

import { useState } from "react";
import { Copy, Pencil, Reply, SmilePlus, Trash2 } from "lucide-react";
import { Button, Popover, PopoverContent, PopoverTrigger, toast } from "@/design";
import type { GroupEntry } from "@/lib/api/groups";

/** The few reactions a tap away; any one is a single emoji, and tapping the one you chose takes it back. */
export const QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "🙏", "🎉"];

type Props = {
  entry: GroupEntry;
  onReply: () => void;
  onReact: (emoji: string) => void;
  /** Present only on a message you wrote and have not deleted. */
  onEdit?: () => void;
  onDelete?: () => void;
};

/** What you can do to a message, beside it while you point at it: reply, react, copy, and on your own, edit or delete. */
export function MessageActions({ entry, onReply, onReact, onEdit, onDelete }: Props) {
  const [picking, setPicking] = useState(false);
  const mine = entry.reactions?.find((r) => r.from_user)?.emoji ?? "";
  return (
    <div className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100">
      <Button variant="ghost" size="icon-sm" aria-label={`Reply to ${entry.sender}`} onClick={onReply}>
        <Reply />
      </Button>
      <Popover open={picking} onOpenChange={setPicking}>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="React">
            <SmilePlus />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="center" className="flex w-auto gap-1 p-1.5">
          {QUICK_REACTIONS.map((emoji) => (
            <Button key={emoji} variant={emoji === mine ? "secondary" : "ghost"} size="icon-sm" aria-label={`React with ${emoji}`} aria-pressed={emoji === mine} onClick={() => {
                setPicking(false);
                onReact(emoji === mine ? "" : emoji);
              }}>
              <span className="text-base leading-none">{emoji}</span>
            </Button>
          ))}
        </PopoverContent>
      </Popover>
      {entry.text && (
        <Button variant="ghost" size="icon-sm" aria-label="Copy message" onClick={() => void navigator.clipboard.writeText(entry.text).then(() => toast.success("Copied"))}>
          <Copy />
        </Button>
      )}
      {onEdit && (
        <Button variant="ghost" size="icon-sm" aria-label="Edit message" onClick={onEdit}>
          <Pencil />
        </Button>
      )}
      {onDelete && (
        <Button variant="ghost" size="icon-sm" aria-label="Delete message" onClick={onDelete}>
          <Trash2 />
        </Button>
      )}
    </div>
  );
}
