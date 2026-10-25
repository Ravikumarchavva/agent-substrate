import { Bot } from "lucide-react";
import { cn } from "@/design";
import { buildObjectUrl } from "@/lib/api/_client";
import { hueOf, initialOf } from "./text";

export type AvatarProps = {
  name: string;
  src?: string | null;
  className?: string;
  /** Whether this avatar represents an AI bot / agent */
  isBot?: boolean;
  /** Optional custom class for the attached bot badge */
  badgeClassName?: string;
};

/** A person's or agent's picture, or their initial in their own colour when they have none. */
export function Avatar({ name, src, className, isBot, badgeClassName }: AvatarProps) {
  const content = src ? (
    // eslint-disable-next-line @next/next/no-img-element -- an authenticated, already-resized 256px picture: nothing for next/image to optimise
    <img src={buildObjectUrl(src)} alt="" aria-hidden className={cn("size-9 shrink-0 rounded-full object-cover", className)} />
  ) : (
    <span
      aria-hidden
      className={cn("flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold", className)}
      style={{ backgroundColor: `hsl(${hueOf(name)} 60% 55% / 0.18)`, color: `hsl(${hueOf(name)} 55% 45%)` }}
    >
      {initialOf(name)}
    </span>
  );

  if (!isBot) {
    return content;
  }

  return (
    <span className="relative inline-flex shrink-0">
      {content}
      <span
        aria-label="Bot agent"
        title="Agent"
        className={cn(
          "absolute -bottom-0.5 -right-0.5 flex size-4 items-center justify-center rounded-full bg-accent text-accent-foreground ring-2 ring-card shadow-xs",
          badgeClassName
        )}
      >
        <Bot className="size-[65%] text-current" strokeWidth={2.5} />
      </span>
    </span>
  );
}
