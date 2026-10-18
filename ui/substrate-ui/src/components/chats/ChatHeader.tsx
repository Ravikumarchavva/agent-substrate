import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { Button, cn } from "@/design";

type Props = {
  /** Who it is: a picture, or a few overlapping ones. */
  avatar: ReactNode;
  title: string;
  /** The line under the name: who is in it, what it is for, or "typing…". */
  subtitle: string;
  /** Shown in the live colour while someone is typing. */
  live?: boolean;
  onBack?: () => void;
  /** The buttons on the right: info, files… */
  children?: ReactNode;
};

/** The top of every conversation, a group's or an agent's: back (on narrow screens), who it is, what is happening, and its buttons. */
export function ChatHeader({ avatar, title, subtitle, live, onBack, children }: Props) {
  return (
    <header className="flex items-center gap-3 border-b border-border bg-background px-4 pb-3 pt-16 sm:pt-3">
      {onBack && (
        <Button variant="ghost" size="icon" aria-label="Back to chats" onClick={onBack} className="xl:hidden">
          <ArrowLeft />
        </Button>
      )}
      {avatar}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-base font-semibold text-foreground">{title}</h1>
        <p className={cn("truncate text-xs", live ? "font-medium text-success" : "text-muted")}>{subtitle}</p>
      </div>
      {children}
    </header>
  );
}
