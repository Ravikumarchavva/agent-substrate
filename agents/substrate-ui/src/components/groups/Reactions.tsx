"use client";

import { Button, cn } from "@/design";
import type { Reaction } from "@/lib/api/groups";

/** The reactions on a message, one chip per emoji with how many chose it; yours is highlighted, and tapping a chip is choosing it (or taking yours back). */
export function Reactions({ reactions, onReact }: { reactions: Reaction[]; onReact: (emoji: string) => void }) {
  if (reactions.length === 0) return null;
  const byEmoji = new Map<string, Reaction[]>();
  for (const r of reactions) byEmoji.set(r.emoji, [...(byEmoji.get(r.emoji) ?? []), r]);
  return (
    <ul className="-mb-1 mt-1 flex flex-wrap gap-1">
      {[...byEmoji].map(([emoji, who]) => {
        const mine = who.some((r) => r.from_user);
        return (
          <li key={emoji}>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onReact(mine ? "" : emoji)}
              title={who.map((r) => r.sender).join(", ")}
              aria-pressed={mine}
              aria-label={`${emoji} from ${who.map((r) => r.sender).join(", ")}`}
              className={cn("gap-1 rounded-full border px-2 text-xs font-normal", mine ? "border-accent bg-accent/15 text-foreground hover:bg-accent/20" : "border-border bg-background/60 text-muted hover:bg-card-hover")}
            >
              <span>{emoji}</span>
              {who.length > 1 && <span className="tabular-nums">{who.length}</span>}
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
