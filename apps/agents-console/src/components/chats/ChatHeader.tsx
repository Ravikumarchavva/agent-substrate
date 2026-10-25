import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { Button, cn } from "@/design";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";

type Props = {
  /** Who it is: a picture, or a few overlapping ones. */
  avatar: ReactNode;
  title: string;
  /** The line under the name: who is in it, what it is for, or "typing…". */
  subtitle: string;
  /** Shown in the live colour while someone is typing. */
  live?: boolean;
  onBack?: () => void;
  /** Toggle the secondary chats list on desktop. */
  onToggleList?: () => void;
  isListOpen?: boolean;
  /** The buttons on the right: info, files… */
  children?: ReactNode;
};

/** The top of every conversation, a group's or an agent's: back (on narrow screens), who it is, what is happening, and its buttons. */
export function ChatHeader({ avatar, title, subtitle, live, onBack, onToggleList, isListOpen, children }: Props) {
  return (
    <header className="flex items-center gap-3 border-b border-border bg-background px-4 pb-3 pt-16 sm:pt-3">
      {onBack && (
        <Button variant="ghost" size="icon" aria-label="Back to chats" onClick={onBack} className="xl:hidden">
          <ArrowLeft />
        </Button>
      )}
      {onToggleList && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={isListOpen ? "Hide chat list" : "Show chat list"}
          title={isListOpen ? "Hide chat list" : "Show chat list"}
          aria-pressed={isListOpen}
          onClick={onToggleList}
          className="hidden @4xl:inline-flex text-muted hover:text-foreground"
        >
          <SidebarToggleIcon direction={isListOpen ? "close" : "open"} className="h-4 w-4" />
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
