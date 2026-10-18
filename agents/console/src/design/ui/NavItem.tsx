import { Slot } from "@radix-ui/react-slot";
import type { LucideIcon } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../cn";

export interface NavItemProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: LucideIcon;
  /** The page or view this item opens is the one showing now. */
  active?: boolean;
  /** A count shown at the end (unread, waiting…). Hidden at 0. */
  badge?: number;
  asChild?: boolean;
}

/**
 * Every row in a navigation list: the sidebar's New chat / Scheduled / Approvals, the conversations, Archived, and the settings pages.
 * One height (the large control), one hover, one selected look (violet), so rows in different lists are the same thing.
 */
export const NavItem = forwardRef<HTMLButtonElement, NavItemProps>(({ icon: Icon, active, badge, asChild, className, children, type = "button", ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      ref={ref}
      type={asChild ? undefined : type}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-control-lg w-full cursor-pointer items-center gap-3 rounded-lg px-3 text-left text-sm font-medium text-foreground transition-colors " +
          "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent",
        active ? "bg-accent/12" : "hover:bg-card-hover",
        className,
      )}
      {...props}
    >
      {Icon && <Icon className={cn("size-4 shrink-0", active ? "text-accent" : "text-muted")} aria-hidden />}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {badge !== undefined && badge > 0 && <span className="shrink-0 rounded-full bg-accent px-2 py-0.5 text-2xs font-bold text-accent-foreground">{badge}</span>}
    </Comp>
  );
});
NavItem.displayName = "NavItem";
