"use client";

import { Eye } from "lucide-react";
import { Button } from "@/design";

type Props = {
  name: string;
  onSwitch: () => void;
  onExit: () => void;
};

/** Across the top of an agent's account while you are in it: whose it is, that it is yours to read and not to use, and the way back. */
export function ViewBanner({ name, onSwitch, onExit }: Props) {
  return (
    <div role="status" className="flex items-center gap-3 border-b border-accent/30 bg-accent/10 px-4 py-2 text-sm text-foreground">
      <Eye className="shrink-0 text-accent" aria-hidden />
      <p className="min-w-0 flex-1 truncate">
        Viewing <strong className="font-semibold">{name}</strong>&rsquo;s account <span className="text-muted">· read only: you can look, not write</span>
      </p>
      <Button variant="ghost" size="sm" onClick={onSwitch}>
        Switch agent
      </Button>
      <Button variant="secondary" size="sm" onClick={onExit}>
        Exit
      </Button>
    </div>
  );
}
